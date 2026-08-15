/**
 * Identity Service
 *
 * Provides deterministic normalized identity for player matching.
 * The normalized identity is used to match players across different data sources
 * without relying on fuzzy matching or fuzzy aliases.
 *
 * Rules:
 * - Strip punctuation: '.', "'", '’'
 * - Strip suffix: 'Jr', 'Sr', 'II', 'III', 'IV' (case insensitive)
 * - Lowercase and trim whitespace
 * - Collapse multiple spaces to single space
 */

/**
 * Normalizes a name string deterministically.
 * Returns a canonical form used for identity matching.
 */
export function normalizeIdentity(name: string): string {
  return (
    name
      .toLowerCase()
      // Strip punctuation (including apostrophes used as contractions)
      .replace(/[.'’]/g, '')
      // Strip common suffixes
      .replace(/\b(jr|sr|ii|iii|iv)\b/gi, '')
      // Collapse whitespace
      .replace(/\s+/g, ' ')
      .trim()
  );
}

/**
 * Generate a playerId from a name.
 * This is the stable identity key used for matching.
 */
export function generatePlayerId(name: string): string {
  return normalizeIdentity(name);
}

/**
 * Check if two names would match under the identity algorithm.
 * Both must normalize to the same value.
 */
export function namesMatch(name1: string, name2: string): boolean {
  return normalizeIdentity(name1) === normalizeIdentity(name2);
}

// Position enum values
export const VALID_POSITIONS = ['QB', 'RB', 'WR', 'TE'] as const;
export type Position = (typeof VALID_POSITIONS)[number];

/**
 * Validates if a position string is valid.
 */
export function isValidPosition(position: string): position is Position {
  return VALID_POSITIONS.includes(position.toUpperCase() as Position);
}
