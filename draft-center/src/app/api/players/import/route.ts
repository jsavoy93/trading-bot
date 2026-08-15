import { NextRequest, NextResponse } from 'next/server';
import { generateImportPreview, executeImport, type ImportMode } from '@/services/import-service';

/**
 * POST /api/players/import
 *
 * Import players from CSV.
 *
 * Body:
 * - csv: CSV content string
 * - mode: "MERGE" | "REPLACE"
 * - preview: boolean (if true, only generate preview without executing)
 */
export async function POST(request: NextRequest) {
  try {
    const body = await request.json();
    const { csv, mode, preview } = body as {
      csv?: string;
      mode?: ImportMode;
      preview?: boolean;
    };

    if (!csv || typeof csv !== 'string') {
      return NextResponse.json(
        { error: 'CSV content is required' },
        { status: 400 }
      );
    }

    if (!mode || (mode !== 'MERGE' && mode !== 'REPLACE')) {
      return NextResponse.json(
        { error: 'Mode must be "MERGE" or "REPLACE"' },
        { status: 400 }
      );
    }

    if (preview === true) {
      // Generate preview without making changes
      const result = await generateImportPreview(csv);
      return NextResponse.json(result);
    } else {
      // Execute actual import
      const result = await executeImport(csv, mode);

      if (!result.success) {
        return NextResponse.json(
          { error: 'Import failed', details: result.errors },
          { status: 400 }
        );
      }

      return NextResponse.json({
        success: true,
        mode: result.mode,
        created: result.created,
        updated: result.updated,
        deactivated: result.deactivated,
      });
    }
  } catch (error) {
    console.error('Error importing players:', error);
    return NextResponse.json(
      { error: 'Failed to import players' },
      { status: 500 }
    );
  }
}
