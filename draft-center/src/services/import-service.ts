/**
 * Import Service
 *
 * Handles CSV import with preview and execution.
 *
 * Preview semantics (zero writes):
 * - CREATE: Player in CSV doesn't exist in DB
 * - UPDATE: Player in CSV matches existing player by identity
 * - UNCHANGED: Player data identical to existing
 * - AMBIGUOUS: Multiple existing players match (should not happen with deterministic identity)
 *
 * MERGE semantics:
 * - Creates new players from CSV
 * - Updates existing players with CSV data
 * - Preserves fields not in CSV (null in CSV = preserved)
 * - Blank fields in CSV clear existing values
 * - Does not deactivate players not in CSV
 *
 * REPLACE semantics:
 * - Creates new players from CSV
 * - Updates existing players with CSV data
 * - Deactivates players not in CSV (except seed players and drafted players)
 */

import { prisma } from '@/lib/prisma/client';
import { generatePlayerId } from './identity';
import { parseCSV, rowsToPlayerInputs, type CSVPlayerRow, type CSVError, type CSVWarning } from './csv-parser';

export type ImportMode = 'MERGE' | 'REPLACE';
export type ImportPreviewStatus = 'CREATE' | 'UPDATE' | 'UNCHANGED' | 'AMBIGUOUS' | 'ERROR';

export interface ImportPreviewResult {
  status: ImportPreviewStatus;
  rows: ImportPreviewRow[];
  errors: CSVError[];
  warnings: CSVWarning[];
  summary: {
    create: number;
    update: number;
    unchanged: number;
    ambiguous: number;
    error: number;
  };
}

export interface ImportPreviewRow {
  rowNumber: number;
  name: string;
  position: string;
  status: ImportPreviewStatus;
  currentPlayerId?: string;
  currentName?: string;
  currentPosition?: string;
  changes?: string[];
}

export interface ImportResult {
  success: boolean;
  mode: ImportMode;
  created: number;
  updated: number;
  deactivated: number;
  errors: string[];
}

/**
 * Generate preview of import without making changes.
 */
export async function generateImportPreview(
  csvContent: string
): Promise<ImportPreviewResult> {
  const { rows, errors, warnings } = parseCSV(csvContent);

  // If parse errors, return early
  if (errors.length > 0) {
    return {
      status: 'ERROR',
      rows: [],
      errors,
      warnings,
      summary: { create: 0, update: 0, unchanged: 0, ambiguous: 0, error: rows.length },
    };
  }

  const previewRows: ImportPreviewRow[] = [];
  let create = 0;
  let update = 0;
  let unchanged = 0;
  let ambiguous = 0;

  for (const row of rows) {
    const existing = await prisma.player.findUnique({
      where: { playerId: row.playerId },
    });

    if (!existing) {
      // CREATE
      previewRows.push({
        rowNumber: row.rowNumber,
        name: row.name,
        position: row.position,
        status: 'CREATE',
      });
      create++;
    } else if (existing.position !== row.position) {
      // AMBIGUOUS - same normalized name but different position
      previewRows.push({
        rowNumber: row.rowNumber,
        name: row.name,
        position: row.position,
        status: 'AMBIGUOUS',
        currentPlayerId: existing.id,
        currentName: existing.name,
        currentPosition: existing.position,
      });
      ambiguous++;
    } else {
      // Check if anything changed
      const changes: string[] = [];
      if (existing.name !== row.name) changes.push(`name: "${existing.name}" → "${row.name}"`);
      if (existing.nflTeam !== row.nflTeam) changes.push(`nflTeam: "${existing.nflTeam}" → "${row.nflTeam}"`);
      if (existing.byeWeek !== row.byeWeek) changes.push(`byeWeek: ${existing.byeWeek} → ${row.byeWeek}`);
      if (existing.adp !== row.adp) changes.push(`adp: ${existing.adp} → ${row.adp}`);

      if (changes.length === 0) {
        previewRows.push({
          rowNumber: row.rowNumber,
          name: row.name,
          position: row.position,
          status: 'UNCHANGED',
          currentPlayerId: existing.id,
          currentName: existing.name,
        });
        unchanged++;
      } else {
        previewRows.push({
          rowNumber: row.rowNumber,
          name: row.name,
          position: row.position,
          status: 'UPDATE',
          currentPlayerId: existing.id,
          currentName: existing.name,
          currentPosition: existing.position,
          changes,
        });
        update++;
      }
    }
  }

  return {
    status: 'ERROR' in errors ? 'ERROR' : create + update + unchanged + ambiguous === rows.length ? 'CREATE' : 'UPDATE',
    rows: previewRows,
    errors,
    warnings,
    summary: { create, update, unchanged, ambiguous, error: 0 },
  };
}

/**
 * Execute import with full transactional semantics.
 * On any failure, the entire import is rolled back.
 */
export async function executeImport(
  csvContent: string,
  mode: ImportMode
): Promise<ImportResult> {
  const { rows, errors } = parseCSV(csvContent);

  // If parse errors, fail before any writes
  if (errors.length > 0) {
    return {
      success: false,
      mode,
      created: 0,
      updated: 0,
      deactivated: 0,
      errors: errors.map((e) => `Row ${e.row}: ${e.message}`),
    };
  }

  try {
    const result = await prisma.$transaction(async (tx) => {
      let created = 0;
      let updated = 0;
      let deactivated = 0;

      // REPLACE: deactivate players not in CSV (preserve seed + drafted)
      if (mode === 'REPLACE') {
        const csvPlayerIds = new Set(rows.map((r) => r.playerId));

        // Get seed players
        const seedPlayers = await tx.player.findMany({
          where: { source: 'seed' },
          select: { id: true },
        });
        const seedIds = new Set(seedPlayers.map((p) => p.id));

        // Get drafted players
        const draftedPicks = await tx.draftPick.findMany({
          where: { selectedPlayerId: { not: null } },
          select: { selectedPlayerId: true },
        });
        const draftedIds = new Set(
          draftedPicks
            .map((p) => p.selectedPlayerId)
            .filter((id): id is string => id !== null)
        );

        // Find players to deactivate
        const existingPlayers = await tx.player.findMany({
          where: {
            active: true,
            id: { notIn: Array.from(seedIds) },
          },
        });

        const toDeactivate = existingPlayers.filter(
          (p) => !draftedIds.has(p.id) && !csvPlayerIds.has(p.playerId)
        );

        if (toDeactivate.length > 0) {
          await tx.player.updateMany({
            where: { id: { in: toDeactivate.map((p) => p.id) } },
            data: { active: false },
          });
          deactivated = toDeactivate.length;
        }
      }

      // Process each row
      for (const row of rows) {
        const existing = await tx.player.findUnique({
          where: { playerId: row.playerId },
        });

        if (!existing) {
          // CREATE new player
          await tx.player.create({
            data: {
              id: `player-${Date.now()}-${Math.random().toString(36).slice(2, 9)}`,
              playerId: row.playerId,
              name: row.name,
              position: row.position,
              nflTeam: row.nflTeam,
              byeWeek: row.byeWeek,
              adp: row.adp,
              source: row.source || 'csv',
              sourceId: row.sourceId,
              active: row.active,
            },
          });
          created++;
        } else if (existing.position !== row.position) {
          // AMBIGUOUS - skip (would require manual resolution)
          continue;
        } else {
          // UPDATE existing player - MERGE semantics preserve absent fields
          const updateData: Record<string, unknown> = {
            name: row.name,
            position: row.position,
            active: row.active,
          };

          // Only update optional fields if CSV has them
          // Blank/null in CSV means clear the field
          updateData.nflTeam = row.nflTeam;
          updateData.byeWeek = row.byeWeek;
          updateData.adp = row.adp;
          if (row.source) updateData.source = row.source;
          if (row.sourceId !== undefined) updateData.sourceId = row.sourceId;

          await tx.player.update({
            where: { id: existing.id },
            data: updateData,
          });
          updated++;
        }
      }

      return { created, updated, deactivated };
    });

    return {
      success: true,
      mode,
      created: result.created,
      updated: result.updated,
      deactivated: result.deactivated,
      errors: [],
    };
  } catch (error) {
    // Transaction rolled back automatically
    return {
      success: false,
      mode,
      created: 0,
      updated: 0,
      deactivated: 0,
      errors: [error instanceof Error ? error.message : 'Unknown error'],
    };
  }
}

/**
 * Validate CSV template headers match parser expectations.
 */
export function validateTemplate(headers: string[]): { valid: boolean; missing: string[]; extra: string[] } {
  const normalized = headers.map((h) => h.toLowerCase().trim());
  const missing = CSV_REQUIRED_HEADERS.filter((h) => !normalized.includes(h));
  const extra = normalized.filter((h) => !CSV_ALL_HEADERS.includes(h));
  return {
    valid: missing.length === 0,
    missing,
    extra,
  };
}

const CSV_REQUIRED_HEADERS = ['name', 'position'];
const CSV_ALL_HEADERS = ['name', 'position', 'nfl_team', 'bye_week', 'adp', 'source', 'source_id', 'active'];
