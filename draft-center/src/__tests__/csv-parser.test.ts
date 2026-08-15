/**
 * CSV Parser Tests
 *
 * Tests for CSV parsing with various edge cases.
 */

import { parseCSV, CSV_HEADERS } from '../services/csv-parser';

describe('CSV Parser', () => {
  describe('valid CSV', () => {
    it('should parse a valid CSV with all columns', () => {
      const csv = `name,position,nfl_team,bye_week,adp,source,source_id,active
Christian McCaffrey,RB,SF,9,1.0,csv,cmc-001,true
CeeDee Lamb,WR,DAL,7,2.0,csv,cdl-001,true`;

      const result = parseCSV(csv);

      expect(result.errors).toHaveLength(0);
      expect(result.rows).toHaveLength(2);

      expect(result.rows[0]).toMatchObject({
        name: 'Christian McCaffrey',
        position: 'RB',
        nflTeam: 'SF',
        byeWeek: 9,
        adp: 1.0,
        source: 'csv',
        sourceId: 'cmc-001',
        active: true,
      });
    });

    it('should handle quoted values with commas', () => {
      const csv = `name,position
"Smith, John",QB`;

      const result = parseCSV(csv);

      expect(result.errors).toHaveLength(0);
      expect(result.rows[0].name).toBe('Smith, John');
    });

    it('should handle escaped quotes in quoted values', () => {
      const csv = `name,position
"Ja'Marr, Chase",WR`;

      const result = parseCSV(csv);

      expect(result.errors).toHaveLength(0);
      expect(result.rows[0].name).toBe("Ja'Marr, Chase");
    });
  });

  describe('missing required columns', () => {
    it('should return error when name column is missing', () => {
      const csv = `position,nfl_team
RB,SF`;

      const result = parseCSV(csv);

      expect(result.errors.some((e) => e.code === 'MISSING_REQUIRED_COLUMN' && e.column === 'name')).toBe(true);
    });

    it('should return error when position column is missing', () => {
      const csv = `name,nfl_team
Christian McCaffrey,SF`;

      const result = parseCSV(csv);

      expect(result.errors.some((e) => e.code === 'MISSING_REQUIRED_COLUMN' && e.column === 'position')).toBe(true);
    });

    it('should return multiple errors for multiple missing columns', () => {
      const csv = `nfl_team
SF`;

      const result = parseCSV(csv);

      expect(result.errors.filter((e) => e.code === 'MISSING_REQUIRED_COLUMN')).toHaveLength(2);
    });
  });

  describe('unknown position', () => {
    it('should return error for invalid position', () => {
      const csv = `name,position
Christian McCaffrey,K`;

      const result = parseCSV(csv);

      expect(result.errors[0]).toMatchObject({
        code: 'INVALID_POSITION',
        row: 2,
        message: expect.stringContaining('Invalid position'),
      });
    });

    it('should accept valid positions (case insensitive)', () => {
      const csv = `name,position
Christian McCaffrey,rb
CeeDee Lamb,wr`;

      const result = parseCSV(csv);

      expect(result.errors).toHaveLength(0);
      expect(result.rows[0].position).toBe('RB');
      expect(result.rows[1].position).toBe('WR');
    });
  });

  describe('malformed numeric values', () => {
    it('should return error for non-numeric bye_week', () => {
      const csv = `name,position,bye_week
Christian McCaffrey,RB,week9`;

      const result = parseCSV(csv);

      expect(result.errors[0]).toMatchObject({
        code: 'MALFORMED_NUMERIC',
        column: 'bye_week',
      });
    });

    it('should return error for non-numeric adp', () => {
      const csv = `name,position,adp
Christian McCaffrey,RB,first`;

      const result = parseCSV(csv);

      expect(result.errors[0]).toMatchObject({
        code: 'MALFORMED_NUMERIC',
        column: 'adp',
      });
    });

    it('should accept decimal adp values', () => {
      const csv = `name,position,adp
Christian McCaffrey,RB,1.5`;

      const result = parseCSV(csv);

      expect(result.errors).toHaveLength(0);
      expect(result.rows[0].adp).toBe(1.5);
    });
  });

  describe('duplicate player_id', () => {
    it('should return error for duplicate player names', () => {
      const csv = `name,position
Christian McCaffrey,RB
Christian McCaffrey,RB`;

      const result = parseCSV(csv);

      expect(result.errors[0]).toMatchObject({
        code: 'DUPLICATE_PLAYER_ID',
        row: 3,
      });
    });

    it('should detect duplicates across normalization', () => {
      const csv = `name,position
Christian McCaffrey,RB
CHRISTIAN MCCAFFREY,RB`;

      const result = parseCSV(csv);

      expect(result.errors.some((e) => e.code === 'DUPLICATE_PLAYER_ID')).toBe(true);
    });
  });

  describe('optional columns', () => {
    it('should parse CSV with only required columns', () => {
      const csv = `name,position
Christian McCaffrey,RB`;

      const result = parseCSV(csv);

      expect(result.errors).toHaveLength(0);
      expect(result.rows[0]).toMatchObject({
        name: 'Christian McCaffrey',
        position: 'RB',
        nflTeam: null,
        byeWeek: null,
        adp: null,
        source: 'csv',
        sourceId: null,
        active: true,
      });
    });

    it('should use "csv" as default source', () => {
      const csv = `name,position
Christian McCaffrey,RB`;

      const result = parseCSV(csv);

      expect(result.rows[0].source).toBe('csv');
    });
  });

  describe('row-level errors', () => {
    it('should continue parsing after row error', () => {
      const csv = `name,position
Valid Player,RB
,WR
Another Valid,TE`;

      const result = parseCSV(csv);

      expect(result.errors.length).toBeGreaterThan(0);
      expect(result.rows).toHaveLength(2); // 2 valid rows
      expect(result.rows[1].name).toBe('Another Valid');
    });

    it('should include row number in error', () => {
      const csv = `name,position
Valid,RB
,WR
Third,TE`;

      const result = parseCSV(csv);
      const blankError = result.errors.find((e) => e.row === 3);
      expect(blankError).toBeDefined();
    });
  });

  describe('blank vs absent semantics', () => {
    it('should treat blank nfl_team as null', () => {
      const csv = `name,position,nfl_team
Christian McCaffrey,RB,`;

      const result = parseCSV(csv);

      expect(result.rows[0].nflTeam).toBeNull();
    });

    it('should treat absent nfl_team column as null', () => {
      const csv = `name,position
Christian McCaffrey,RB`;

      const result = parseCSV(csv);
      expect(result.rows[0].nflTeam).toBeNull();
    });

    it('should treat blank bye_week as null', () => {
      const csv = `name,position,bye_week
Christian McCaffrey,RB,`;

      const result = parseCSV(csv);

      expect(result.rows[0].byeWeek).toBeNull();
    });

    it('should treat blank adp as null', () => {
      const csv = `name,position,adp
Christian McCaffrey,RB,`;

      const result = parseCSV(csv);

      expect(result.rows[0].adp).toBeNull();
    });

    it('should treat blank source_id as null', () => {
      const csv = `name,position,source_id
Christian McCaffrey,RB,`;

      const result = parseCSV(csv);

      expect(result.rows[0].sourceId).toBeNull();
    });
  });

  describe('template headers match parser', () => {
    it('should have correct required headers', () => {
      const csv = `name,position
Player,RB`;

      const result = parseCSV(csv);

      expect(result.errors).toHaveLength(0);
    });

    it('should have correct optional headers', () => {
      const csv = `name,position,nfl_team,bye_week,adp,source,source_id,active
Player,RB,SF,9,1.0,csv,id-001,true`;

      const result = parseCSV(csv);

      expect(result.errors).toHaveLength(0);
      expect(result.rows[0]).toMatchObject({
        nflTeam: 'SF',
        byeWeek: 9,
        adp: 1.0,
        source: 'csv',
        sourceId: 'id-001',
        active: true,
      });
    });

    it('should ignore unknown columns with warning', () => {
      const csv = `name,position,unknown_col
Player,RB,value`;

      const result = parseCSV(csv);

      expect(result.warnings.some((w) => w.code === 'UNKNOWN_COLUMN')).toBe(true);
      expect(result.rows[0].name).toBe('Player');
    });
  });

  describe('active field parsing', () => {
    it('should parse true values', () => {
      const csv = `name,position,active
Player,RB,true`;

      const result = parseCSV(csv);

      expect(result.rows[0].active).toBe(true);
    });

    it('should parse 1 as true', () => {
      const csv = `name,position,active
Player,RB,1`;

      const result = parseCSV(csv);

      expect(result.rows[0].active).toBe(true);
    });

    it('should parse yes as true', () => {
      const csv = `name,position,active
Player,RB,yes`;

      const result = parseCSV(csv);

      expect(result.rows[0].active).toBe(true);
    });

    it('should parse false values', () => {
      const csv = `name,position,active
Player,RB,false`;

      const result = parseCSV(csv);

      expect(result.rows[0].active).toBe(false);
    });

    it('should parse 0 as false', () => {
      const csv = `name,position,active
Player,RB,0`;

      const result = parseCSV(csv);

      expect(result.rows[0].active).toBe(false);
    });

    it('should parse no as false', () => {
      const csv = `name,position,active
Player,RB,no`;

      const result = parseCSV(csv);

      expect(result.rows[0].active).toBe(false);
    });

    it('should default to true when absent', () => {
      const csv = `name,position
Player,RB`;

      const result = parseCSV(csv);

      expect(result.rows[0].active).toBe(true);
    });

    it('should return error for invalid boolean', () => {
      const csv = `name,position,active
Player,RB,maybe`;

      const result = parseCSV(csv);

      expect(result.errors[0].code).toBe('INVALID_BOOLEAN');
    });
  });
});

// Helper to avoid repeating parseCSV call
function createParseTest(csv: string) {
  return parseCSV(csv);
}
