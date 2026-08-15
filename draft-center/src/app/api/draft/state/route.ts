import { NextRequest, NextResponse } from 'next/server';
import { prisma } from '@/lib/prisma/client';
import { v4 as uuidv4 } from 'uuid';

/**
 * GET /api/draft/state
 *
 * Returns the current draft state from Prisma.
 */
export async function GET() {
  try {
    const league = await prisma.leagueConfig.findFirst();
    const teams = await prisma.draftPick.findMany({
      orderBy: { overallPick: 'asc' },
    });

    return NextResponse.json({
      league,
      picks: teams,
    });
  } catch (error) {
    console.error('Error fetching draft state:', error);
    return NextResponse.json(
      { error: 'Failed to fetch draft state' },
      { status: 500 }
    );
  }
}

/**
 * POST /api/draft/state
 *
 * Records a pick.
 *
 * Body:
 * - pickNumber: overall pick number
 * - playerId: selected player ID
 */
export async function POST(request: NextRequest) {
  try {
    const body = await request.json();
    const { pickNumber, playerId } = body as {
      pickNumber?: number;
      playerId?: string;
    };

    if (!pickNumber || !playerId) {
      return NextResponse.json(
        { error: 'pickNumber and playerId are required' },
        { status: 400 }
      );
    }

    // Update the draft pick
    const pick = await prisma.draftPick.update({
      where: { overallPick: pickNumber },
      data: {
        selectedPlayerId: playerId,
        selectedAt: new Date(),
      },
    });

    // Mark player as drafted
    await prisma.player.update({
      where: { id: playerId },
      data: { draftedAt: new Date() },
    });

    return NextResponse.json({ success: true, pick });
  } catch (error) {
    console.error('Error recording pick:', error);
    return NextResponse.json(
      { error: 'Failed to record pick' },
      { status: 500 }
    );
  }
}
