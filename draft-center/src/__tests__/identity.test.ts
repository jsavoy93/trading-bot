/**
 * Identity Service Tests
 *
 * Tests for deterministic identity normalization.
 */

import { normalizeIdentity, generatePlayerId, namesMatch, isValidPosition, VALID_POSITIONS } from '../services/identity';

describe('Identity Service', () => {
  describe('normalizeIdentity', () => {
    it('should lowercase the name', () => {
      expect(normalizeIdentity('Christian McCaffrey')).toBe('christian mccaffrey');
      expect(normalizeIdentity('JOSH ALLEN')).toBe('josh allen');
    });

    it('should strip punctuation', () => {
      expect(normalizeIdentity("Ja'Marr Chase")).toBe('jamarr chase');
      expect(normalizeIdentity("O.J. Henry")).toBe('oj henry');
      expect(normalizeIdentity("Patrick Mahomes II")).toBe('patrick mahomes');
    });

    it('should strip suffixes (jr, sr, ii, iii, iv)', () => {
      expect(normalizeIdentity('John Smith Jr')).toBe('john smith');
      expect(normalizeIdentity('John Smith Sr')).toBe('john smith');
      expect(normalizeIdentity('John Smith II')).toBe('john smith');
      expect(normalizeIdentity('John Smith III')).toBe('john smith');
      expect(normalizeIdentity('John Smith IV')).toBe('john smith');
      // Case insensitive
      expect(normalizeIdentity('John Smith SR')).toBe('john smith');
    });

    it('should collapse whitespace', () => {
      expect(normalizeIdentity('  John   Smith  ')).toBe('john smith');
      expect(normalizeIdentity('John\tSmith')).toBe('john smith');
    });

    it('should be deterministic (same input = same output)', () => {
      const name = "Ja'Marr Chase";
      expect(normalizeIdentity(name)).toBe(normalizeIdentity(name));
      expect(normalizeIdentity(name)).toBe(normalizeIdentity(name));
    });
  });

  describe('generatePlayerId', () => {
    it('should generate stable playerId from name', () => {
      const id1 = generatePlayerId('Christian McCaffrey');
      const id2 = generatePlayerId('Christian McCaffrey');
      expect(id1).toBe(id2);
    });

    it('should generate different IDs for different names', () => {
      const id1 = generatePlayerId('Christian McCaffrey');
      const id2 = generatePlayerId('CeeDee Lamb');
      expect(id1).not.toBe(id2);
    });

    it('should normalize before generating ID', () => {
      expect(generatePlayerId('Christian McCaffrey')).toBe(generatePlayerId('CHRISTIAN MCCAFFREY'));
      // Apostrophe is stripped but 's' is preserved
      expect(generatePlayerId("Christian McCaffrey's")).toBe('christian mccaffreys');
      expect(generatePlayerId("Ja'Marr Chase")).toBe(generatePlayerId('Jamarr Chase'));
    });
  });

  describe('namesMatch', () => {
    it('should match names with different casing', () => {
      expect(namesMatch('John Smith', 'JOHN SMITH')).toBe(true);
      expect(namesMatch('john smith', 'John Smith')).toBe(true);
    });

    it('should match names with punctuation differences', () => {
      // Apostrophe is stripped, so "Ja'Marr" becomes "jamarr" not "jamar"
      expect(namesMatch("Ja'Marr Chase", 'Jamarr Chase')).toBe(true);
      expect(namesMatch("O.J. Henry", 'OJ Henry')).toBe(true);
      // Note: "Jamar" would NOT match "Ja'Marr" because the identity is deterministic
      // and doesn't do phonetic normalization
      expect(namesMatch("Christian McCaffrey", "Christian McCaffrey's")).toBe(false); // 's preserved
    });

    it('should match names with suffix differences', () => {
      expect(namesMatch('John Smith Jr', 'John Smith')).toBe(true);
      expect(namesMatch('John Smith', 'John Smith II')).toBe(true);
    });

    it('should not match different names', () => {
      expect(namesMatch('John Smith', 'Jane Smith')).toBe(false);
      expect(namesMatch('Patrick Mahomes', 'Josh Allen')).toBe(false);
    });
  });

  describe('isValidPosition', () => {
    it('should accept valid positions', () => {
      expect(isValidPosition('QB')).toBe(true);
      expect(isValidPosition('RB')).toBe(true);
      expect(isValidPosition('WR')).toBe(true);
      expect(isValidPosition('TE')).toBe(true);
      // Case insensitive
      expect(isValidPosition('qb')).toBe(true);
      expect(isValidPosition('Rb')).toBe(true);
    });

    it('should reject invalid positions', () => {
      expect(isValidPosition('K')).toBe(false);
      expect(isValidPosition('DEF')).toBe(false);
      expect(isValidPosition('Flex')).toBe(false);
      expect(isValidPosition('')).toBe(false);
      expect(isValidPosition('QB ')).toBe(false); // Trailing space
    });
  });

  describe('VALID_POSITIONS', () => {
    it('should contain exactly the four fantasy positions', () => {
      expect(VALID_POSITIONS).toEqual(['QB', 'RB', 'WR', 'TE']);
    });
  });
});
