import { NextRequest, NextResponse } from 'next/server';
import { getAvailablePlayers, searchAvailablePlayers, getAllPlayers } from '@/services/player-service';

/**
 * GET /api/players
 *
 * Returns available players (active, not drafted).
 * Query params:
 * - q: search query (optional)
 * - all: if "true", returns all players including inactive (for admin)
 */
export async function GET(request: NextRequest) {
  try {
    const { searchParams } = new URL(request.url);
    const query = searchParams.get('q');
    const all = searchParams.get('all') === 'true';

    let players;

    if (all) {
      // Admin endpoint - return all players
      players = await getAllPlayers();
    } else if (query) {
      players = await searchAvailablePlayers(query);
    } else {
      players = await getAvailablePlayers();
    }

    return NextResponse.json({ players });
  } catch (error) {
    console.error('Error fetching players:', error);
    return NextResponse.json(
      { error: 'Failed to fetch players' },
      { status: 500 }
    );
  }
}
