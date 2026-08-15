import { prisma } from '@/lib/prisma/client';
import { generatePlayerId, isValidPosition, type Position } from './identity';

export interface PlayerInput {
  id?: string; // External/stable ID - if provided for existing player, preserved
  name: string;
  position: string;
  nflTeam?: string | null;
  byeWeek?: number | null;
  adp?: number | null;
  source?: string;
  sourceId?: string | null;
  sourceUpdatedAt?: Date | string | null;
}

export interface Player extends PlayerInput {
  id: string;
  playerId: string;
  active: boolean;
  draftedAt: Date | null;
  createdAt: Date;
  updatedAt: Date;
}

export interface FindByIdentityOptions {
  name: string;
  position: string;
}

/**
 * Player Service
 *
 * Governance-required helpers for Phase 4:
 * - bulkUpsert: create or update players in a single transaction
 * - findByIdentity: find a player by normalized identity
 * - getDraftedIds: get IDs of all currently drafted players
 *
 * All mutations are transactional.
 * Existing Player.id values are never changed during updates.
 */

/**
 * bulkUpsert - Create or update players transactionally.
 *
 * - CREATE: Player with new id (or new playerId match) doesn't exist
 * - UPDATE: Player with matching id already exists
 * - Never changes existing player's internal id
 * - Returns created/updated count
 */
export async function bulkUpsert(
  inputs: PlayerInput[],
  mode: 'MERGE' | 'REPLACE'
): Promise<{ created: number; updated: number; deactivated: number }> {
  if (inputs.length === 0) {
    return { created: 0, updated: 0, deactivated: 0 };
  }

  // Validate inputs
  for (const input of inputs) {
    if (!isValidPosition(input.position)) {
      throw new Error(`Invalid position: ${input.position}`);
    }
    if (!input.name || !input.name.trim()) {
      throw new Error('Player name is required');
    }
  }

  return prisma.$transaction(async (tx) => {
    let created = 0;
    let updated = 0;
    let deactivated = 0;

    // Get existing playerIds from CSV inputs
    const csvPlayerIds = new Set(
      inputs.map((i) => generatePlayerId(i.name))
    );

    // REPLACE mode: deactivate players not in CSV
    if (mode === 'REPLACE') {
      // Find seed players that should be preserved
      const seedPlayers = await tx.player.findMany({
        where: { source: 'seed' },
        select: { id: true },
      });
      const seedIds = new Set(seedPlayers.map((p) => p.id));

      // Find drafted players that should be preserved
      const draftedPlayers = await tx.player.findMany({
        where: { draftedAt: { not: null } },
        select: { id: true },
      });
      const draftedIds = new Set(draftedPlayers.map((p) => p.id));

      // Deactivate non-seed, non-drafted players not in CSV
      const existingNotInCsv = await tx.player.findMany({
        where: {
          active: true,
          id: { notIn: Array.from(seedIds) },
          draftedAt: null,
          playerId: { notIn: Array.from(csvPlayerIds) },
        },
      });

      deactivated = existingNotInCsv.length;
      if (deactivated > 0) {
        await tx.player.updateMany({
          where: { id: { in: existingNotInCsv.map((p) => p.id) } },
          data: { active: false },
        });
      }
    }

    // Process each input
    for (const input of inputs) {
      const playerId = generatePlayerId(input.name);

      // Check if player with this id already exists
      const existingById = input.id
        ? await tx.player.findUnique({ where: { id: input.id } })
        : null;

      if (existingById) {
        // UPDATE existing player - preserve internal id
        await tx.player.update({
          where: { id: existingById.id },
          data: {
            name: input.name,
            playerId,
            position: input.position,
            nflTeam: input.nflTeam ?? null,
            byeWeek: input.byeWeek ?? null,
            adp: input.adp ?? null,
            source: input.source ?? 'csv',
            sourceId: input.sourceId ?? null,
            sourceUpdatedAt: input.sourceUpdatedAt
              ? new Date(input.sourceUpdatedAt)
              : null,
            active: true, // Reactivate if was deactivated
          },
        });
        updated++;
      } else {
        // Check if player with this playerId exists (match by normalized name)
        const existingByPlayerId = await tx.player.findUnique({
          where: { playerId },
        });

        if (existingByPlayerId) {
          // UPDATE existing player matched by identity - preserve internal id
          await tx.player.update({
            where: { id: existingByPlayerId.id },
            data: {
              name: input.name, // Update name in case it changed
              position: input.position,
              nflTeam: input.nflTeam ?? null,
              byeWeek: input.byeWeek ?? null,
              adp: input.adp ?? null,
              source: input.source ?? 'csv',
              sourceId: input.sourceId ?? null,
              sourceUpdatedAt: input.sourceUpdatedAt
                ? new Date(input.sourceUpdatedAt)
                : null,
              active: true,
            },
          });
          updated++;
        } else {
          // CREATE new player
          const newId = input.id || `player-${Date.now()}-${Math.random().toString(36).slice(2, 9)}`;
          await tx.player.create({
            data: {
              id: newId,
              playerId,
              name: input.name,
              position: input.position,
              nflTeam: input.nflTeam ?? null,
              byeWeek: input.byeWeek ?? null,
              adp: input.adp ?? null,
              source: input.source ?? 'csv',
              sourceId: input.sourceId ?? null,
              sourceUpdatedAt: input.sourceUpdatedAt
                ? new Date(input.sourceUpdatedAt)
                : null,
              active: true,
            },
          });
          created++;
        }
      }
    }

    return { created, updated, deactivated };
  });
}

/**
 * findByIdentity - Find a player by normalized identity.
 *
 * Uses deterministic identity matching based on name + position.
 * Returns null if no player matches.
 */
export async function findByIdentity(
  options: FindByIdentityOptions
): Promise<Player | null> {
  const { name, position } = options;

  if (!isValidPosition(position)) {
    return null;
  }

  const playerId = generatePlayerId(name);

  const player = await prisma.player.findUnique({
    where: { playerId },
  });

  if (!player || player.position !== position) {
    return null;
  }

  return player as Player;
}

/**
 * getDraftedIds - Get IDs of all currently drafted players.
 *
 * Returns set of player IDs that are referenced by draft picks.
 */
export async function getDraftedIds(): Promise<Set<string>> {
  const picks = await prisma.draftPick.findMany({
    where: { selectedPlayerId: { not: null } },
    select: { selectedPlayerId: true },
  });

  return new Set(
    picks
      .map((p) => p.selectedPlayerId)
      .filter((id): id is string => id !== null)
  );
}

/**
 * getAvailablePlayers - Get all active, undrafted players.
 *
 * Excludes active=false and already drafted players.
 */
export async function getAvailablePlayers(): Promise<Player[]> {
  const draftedIds = await getDraftedIds();

  const players = await prisma.player.findMany({
    where: {
      active: true,
      id: { notIn: Array.from(draftedIds) },
    },
    orderBy: [{ adp: 'asc' }, { name: 'asc' }],
  });

  return players as Player[];
}

/**
 * searchAvailablePlayers - Search available players by name.
 *
 * Case-insensitive partial match on normalized name.
 */
export async function searchAvailablePlayers(
  query: string
): Promise<Player[]> {
  const draftedIds = await getDraftedIds();

  const normalizedQuery = generatePlayerId(query);

  const players = await prisma.player.findMany({
    where: {
      active: true,
      id: { notIn: Array.from(draftedIds) },
      playerId: { contains: normalizedQuery },
    },
    orderBy: [{ adp: 'asc' }, { name: 'asc' }],
  });

  return players as Player[];
}

/**
 * Get all players (including inactive) for import preview.
 */
export async function getAllPlayers(): Promise<Player[]> {
  const players = await prisma.player.findMany({
    orderBy: [{ active: 'desc' }, { adp: 'asc' }, { name: 'asc' }],
  });
  return players as Player[];
}
