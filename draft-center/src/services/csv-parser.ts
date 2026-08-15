/**
 * CSV Parser for Player Import
 *
 * Parses CSV files with the following columns:
 * - name (required): Player full name
 * - position (required): QB, RB, WR, TE
 * - nfl_team (optional): NFL team abbreviation
 * - bye_week (optional): Bye week number
 * - adp (optional): Average draft position
 * - source (optional): Source identifier
 * - source_id (optional): External source player ID
 * - active (optional): true/false, defaults to true
 *
 * Behavior:
 * - Blank vs absent: blank string for optional field = null
 * - Headers are case-insensitive and trimmed
 * - Duplicate player_id detection within same import
 * - Row-level errors collected for preview
 */

import { generatePlayerId, isValidPosition, VALID_POSITIONS } from './identity';

export interface CSVParseResult {
  rows: CSVPlayerRow[];
  errors: CSVError[];
  warnings: CSVWarning[];
}

export interface CSVPlayerRow {
  rowNumber: number;
  name: string;
  position: string;
  nflTeam: string | null;
  byeWeek: number | null;
  adp: number | null;
  source: string | null;
  sourceId: string | null;
  active: boolean;
  playerId: string; // Normalized identity
}

export interface CSVError {
  row: number;
  column?: string;
  message: string;
  code: CSVErrorCode;
}

export interface CSVWarning {
  row: number;
  message: string;
  code: CSVWarningCode;
}

export type CSVErrorCode =
  | 'MISSING_REQUIRED_COLUMN'
  | 'INVALID_POSITION'
  | 'MALFORMED_NUMERIC'
  | 'DUPLICATE_PLAYER_ID'
  | 'BLANK_REQUIRED'
  | 'INVALID_BOOLEAN';

export type CSVWarningCode = 'UNKNOWN_COLUMN' | 'EXTRA_WHITESPACE';

export const CSV_HEADERS = [
  'name',
  'position',
  'nfl_team',
  'bye_week',
  'adp',
  'source',
  'source_id',
  'active',
] as const;

export type CSVHeader = (typeof CSV_HEADERS)[number];

const REQUIRED_HEADERS: CSVHeader[] = ['name', 'position'];
const OPTIONAL_HEADERS: CSVHeader[] = ['nfl_team', 'bye_week', 'adp', 'source', 'source_id', 'active'];

/**
 * Parse CSV string into player rows.
 * Returns parsed rows plus any errors/warnings.
 */
export function parseCSV(csvContent: string): CSVParseResult {
  const errors: CSVError[] = [];
  const warnings: CSVWarning[] = [];
  const rows: CSVPlayerRow[] = [];
  const seenPlayerIds = new Map<string, number>(); // playerId -> first row number

  // Normalize line endings
  const normalized = csvContent.replace(/\r\n/g, '\n').replace(/\r/g, '\n');

  // Split into lines
  const lines = normalized.split('\n');

  if (lines.length === 0) {
    errors.push({
      row: 0,
      message: 'Empty CSV file',
      code: 'MISSING_REQUIRED_COLUMN',
    });
    return { rows, errors, warnings };
  }

  // Parse header row
  const headerLine = lines[0];
  const headers = parseCSVLine(headerLine).map((h) => h.toLowerCase().trim());

  // Validate required headers exist
  for (const required of REQUIRED_HEADERS) {
    if (!headers.includes(required)) {
      errors.push({
        row: 0,
        column: required,
        message: `Missing required column: ${required}`,
        code: 'MISSING_REQUIRED_COLUMN',
      });
    }
  }

  // Check for unknown columns
  for (const header of headers) {
    if (!CSV_HEADERS.includes(header as CSVHeader)) {
      warnings.push({
        row: 0,
        message: `Unknown column ignored: ${header}`,
        code: 'UNKNOWN_COLUMN',
      });
    }
  }

  // If missing required headers, can't parse data
  if (errors.length > 0) {
    return { rows, errors, warnings };
  }

  // Build column index map
  const columnIndex = new Map<string, number>();
  headers.forEach((h, i) => columnIndex.set(h, i));

  // Parse data rows
  for (let i = 1; i < lines.length; i++) {
    const lineNum = i + 1; // 1-indexed for user display
    const line = lines[i];

    // Skip empty lines
    if (!line.trim()) {
      continue;
    }

    const values = parseCSVLine(line);

    // Extract fields
    const rawName = getColumn(values, columnIndex, 'name');
    const rawPosition = getColumn(values, columnIndex, 'position');
    const rawNflTeam = getColumn(values, columnIndex, 'nfl_team');
    const rawByeWeek = getColumn(values, columnIndex, 'bye_week');
    const rawAdp = getColumn(values, columnIndex, 'adp');
    const rawSource = getColumn(values, columnIndex, 'source');
    const rawSourceId = getColumn(values, columnIndex, 'source_id');
    const rawActive = getColumn(values, columnIndex, 'active');

    // Validate name (required)
    if (!rawName || !rawName.trim()) {
      errors.push({
        row: lineNum,
        column: 'name',
        message: 'Name is required and cannot be blank',
        code: 'BLANK_REQUIRED',
      });
      continue;
    }

    const name = rawName.trim();
    const playerId = generatePlayerId(name);

    // Validate position (required)
    if (!rawPosition || !rawPosition.trim()) {
      errors.push({
        row: lineNum,
        column: 'position',
        message: 'Position is required and cannot be blank',
        code: 'BLANK_REQUIRED',
      });
      continue;
    }

    const position = rawPosition.trim().toUpperCase();
    if (!isValidPosition(position)) {
      errors.push({
        row: lineNum,
        column: 'position',
        message: `Invalid position: ${position}. Must be one of: ${VALID_POSITIONS.join(', ')}`,
        code: 'INVALID_POSITION',
      });
      continue;
    }

    // Check for duplicate player_id within import
    if (seenPlayerIds.has(playerId)) {
      errors.push({
        row: lineNum,
        column: 'name',
        message: `Duplicate player: ${name} (matches row ${seenPlayerIds.get(playerId)})`,
        code: 'DUPLICATE_PLAYER_ID',
      });
      continue;
    }
    seenPlayerIds.set(playerId, lineNum);

    // Parse optional fields
    let nflTeam: string | null = rawNflTeam?.trim() || null;
    if (nflTeam === '') nflTeam = null;

    let byeWeek: number | null = null;
    if (rawByeWeek && rawByeWeek.trim()) {
      const parsed = parseInt(rawByeWeek.trim(), 10);
      if (isNaN(parsed)) {
        errors.push({
          row: lineNum,
          column: 'bye_week',
          message: `Invalid bye_week value: ${rawByeWeek}. Must be a number.`,
          code: 'MALFORMED_NUMERIC',
        });
        continue;
      }
      byeWeek = parsed;
    }

    let adp: number | null = null;
    if (rawAdp && rawAdp.trim()) {
      const parsed = parseFloat(rawAdp.trim());
      if (isNaN(parsed)) {
        errors.push({
          row: lineNum,
          column: 'adp',
          message: `Invalid adp value: ${rawAdp}. Must be a number.`,
          code: 'MALFORMED_NUMERIC',
        });
        continue;
      }
      adp = parsed;
    }

    let source: string | null = rawSource?.trim() || null;
    if (source === '') source = null;

    let sourceId: string | null = rawSourceId?.trim() || null;
    if (sourceId === '') sourceId = null;

    let active = true;
    if (rawActive && rawActive.trim()) {
      const activeVal = rawActive.trim().toLowerCase();
      if (activeVal === 'true' || activeVal === '1' || activeVal === 'yes') {
        active = true;
      } else if (activeVal === 'false' || activeVal === '0' || activeVal === 'no') {
        active = false;
      } else {
        errors.push({
          row: lineNum,
          column: 'active',
          message: `Invalid active value: ${rawActive}. Must be true/false, 1/0, or yes/no.`,
          code: 'INVALID_BOOLEAN',
        });
        continue;
      }
    }

    // Check for extra whitespace
    if (rawName && rawName !== rawName.trim()) {
      warnings.push({
        row: lineNum,
        message: `Extra whitespace trimmed from name`,
        code: 'EXTRA_WHITESPACE',
      });
    }

    rows.push({
      rowNumber: lineNum,
      name,
      position,
      nflTeam,
      byeWeek,
      adp,
      source: source || 'csv',
      sourceId,
      active,
      playerId,
    });
  }

  return { rows, errors, warnings };
}

/**
 * Parse a single CSV line, handling quoted values.
 */
function parseCSVLine(line: string): string[] {
  const values: string[] = [];
  let current = '';
  let inQuotes = false;

  for (let i = 0; i < line.length; i++) {
    const char = line[i];

    if (char === '"') {
      if (inQuotes && line[i + 1] === '"') {
        // Escaped quote
        current += '"';
        i++;
      } else {
        // Toggle quote mode
        inQuotes = !inQuotes;
      }
    } else if (char === ',' && !inQuotes) {
      values.push(current);
      current = '';
    } else {
      current += char;
    }
  }

  values.push(current);
  return values;
}

/**
 * Get column value by header name, handling missing columns.
 */
function getColumn(
  values: string[],
  columnIndex: Map<string, number>,
  header: string
): string | undefined {
  const index = columnIndex.get(header);
  if (index === undefined || index >= values.length) {
    return undefined;
  }
  return values[index];
}

/**
 * Convert parsed CSV rows to PlayerInput format for bulkUpsert.
 */
export function rowsToPlayerInputs(rows: CSVPlayerRow[]): {
  inputs: Array<{
    name: string;
    position: string;
    nflTeam: string | null;
    byeWeek: number | null;
    adp: number | null;
    source: string;
    sourceId: string | null;
  }>;
  playerIdMap: Map<string, CSVPlayerRow>;
} {
  const inputs: Array<{
    name: string;
    position: string;
    nflTeam: string | null;
    byeWeek: number | null;
    adp: number | null;
    source: string;
    sourceId: string | null;
  }> = [];

  const playerIdMap = new Map<string, CSVPlayerRow>();

  for (const row of rows) {
    inputs.push({
      name: row.name,
      position: row.position,
      nflTeam: row.nflTeam,
      byeWeek: row.byeWeek,
      adp: row.adp,
      source: row.source || 'csv',
      sourceId: row.sourceId,
    });
    playerIdMap.set(row.playerId, row);
  }

  return { inputs, playerIdMap };
}
