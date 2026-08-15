'use client';

import { useState, useEffect, useCallback } from 'react';
import type { Player, DraftPick, Team, LeagueConfig } from '@/lib/types';
import { recordPick, getAvailablePlayers as getAvailablePlayersLogic, searchAvailablePlayers as searchPlayersLogic } from '@/lib/draft-logic';

/**
 * Draft Page - Phase 4 Integration
 *
 * Loads available/master player data from the Prisma-backed /api/players catalog.
 *
 * DraftState.players[] is populated from Prisma via /api/players.
 * It is NOT an independent persistence authority.
 * CSV import modifies Prisma directly via /api/players/import.
 *
 * Available-player search excludes:
 * - active=false players (filtered server-side)
 * - already drafted Player.id values (filtered server-side)
 */

interface DraftState {
  league: LeagueConfig | null;
  teams: Team[];
  picks: DraftPick[];
  players: Player[];
  currentPick: number;
  isLoading: boolean;
  error: string | null;
  searchQuery: string;
}

export default function DraftPage() {
  const [state, setState] = useState<DraftState>({
    league: null,
    teams: [],
    picks: [],
    players: [],
    currentPick: 1,
    isLoading: true,
    error: null,
    searchQuery: '',
  });

  // Load initial state from Prisma
  useEffect(() => {
    async function loadInitialState() {
      try {
        // Load draft state
        const draftRes = await fetch('/api/draft/state');
        const draftData = await draftRes.json();

        // Load available players from Prisma catalog
        const playersRes = await fetch('/api/players');
        const playersData = await playersRes.json();

        const players: Player[] = playersData.players || [];
        const picks: DraftPick[] = draftData.picks || [];

        // Find current pick (first undrafted pick)
        const currentPick = picks.find((p: DraftPick) => !p.selectedPlayerId)?.overallPick || picks.length + 1;

        setState((prev) => ({
          ...prev,
          league: draftData.league || null,
          picks,
          players, // Populated from Prisma
          currentPick,
          isLoading: false,
        }));
      } catch (err) {
        setState((prev) => ({
          ...prev,
          error: 'Failed to load draft state',
          isLoading: false,
        }));
      }
    }

    loadInitialState();
  }, []);

  // Search players from Prisma
  const searchPlayers = useCallback(async (query: string) => {
    setState((prev) => ({ ...prev, searchQuery: query, isLoading: true }));

    try {
      const url = query
        ? `/api/players?q=${encodeURIComponent(query)}`
        : '/api/players';
      const res = await fetch(url);
      const data = await res.json();

      setState((prev) => ({
        ...prev,
        players: data.players || [],
        isLoading: false,
      }));
    } catch {
      setState((prev) => ({
        ...prev,
        error: 'Failed to search players',
        isLoading: false,
      }));
    }
  }, []);

  // Record a pick
  const handlePick = useCallback(
    async (playerId: string) => {
      try {
        const res = await fetch('/api/draft/state', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            pickNumber: state.currentPick,
            playerId,
          }),
        });

        if (!res.ok) {
          throw new Error('Failed to record pick');
        }

        // Update local state
        setState((prev) => {
          const newPicks = prev.picks.map((p) =>
            p.overallPick === prev.currentPick
              ? { ...p, selectedPlayerId: playerId, selectedAt: new Date().toISOString() }
              : p
          );

          // Remove picked player from available list
          const newPlayers = prev.players.filter((pl) => pl.id !== playerId);

          // Find next undrafted pick
          const nextPick =
            newPicks.find((p: DraftPick) => !p.selectedPlayerId)?.overallPick ||
            newPicks.length + 1;

          return {
            ...prev,
            picks: newPicks,
            players: newPlayers,
            currentPick: nextPick,
          };
        });
      } catch (err) {
        setState((prev) => ({
          ...prev,
          error: 'Failed to record pick',
        }));
      }
    },
    [state.currentPick]
  );

  if (state.isLoading) {
    return (
      <div style={{ padding: '2rem', textAlign: 'center' }}>
        <h1>Loading Draft...</h1>
      </div>
    );
  }

  if (state.error) {
    return (
      <div style={{ padding: '2rem', textAlign: 'center', color: 'red' }}>
        <h1>Error</h1>
        <p>{state.error}</p>
      </div>
    );
  }

  const draftedCount = state.picks.filter((p) => p.selectedPlayerId).length;
  const totalPicks = state.picks.length;

  return (
    <div style={{ padding: '2rem', maxWidth: '1200px', margin: '0 auto' }}>
      <header style={{ marginBottom: '2rem' }}>
        <h1>🏈 Draft Center</h1>
        <p>
          Pick {state.currentPick} of {totalPicks} | {draftedCount} players drafted
        </p>
      </header>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '2rem' }}>
        {/* Available Players Panel */}
        <section>
          <h2>Available Players</h2>
          <div style={{ marginBottom: '1rem' }}>
            <input
              type="text"
              placeholder="Search players..."
              value={state.searchQuery}
              onChange={(e) => {
                setState((prev) => ({ ...prev, searchQuery: e.target.value }));
                searchPlayers(e.target.value);
              }}
              style={{
                width: '100%',
                padding: '0.5rem',
                fontSize: '1rem',
                border: '1px solid #ccc',
                borderRadius: '4px',
              }}
            />
          </div>

          <div
            style={{
              border: '1px solid #ddd',
              borderRadius: '8px',
              maxHeight: '500px',
              overflow: 'auto',
            }}
          >
            <table style={{ width: '100%', borderCollapse: 'collapse' }}>
              <thead style={{ position: 'sticky', top: 0, background: '#f5f5f5' }}>
                <tr>
                  <th style={{ padding: '0.5rem', textAlign: 'left' }}>Name</th>
                  <th style={{ padding: '0.5rem', textAlign: 'left' }}>Pos</th>
                  <th style={{ padding: '0.5rem', textAlign: 'left' }}>Team</th>
                  <th style={{ padding: '0.5rem', textAlign: 'right' }}>ADP</th>
                  <th style={{ padding: '0.5rem', textAlign: 'center' }}>Action</th>
                </tr>
              </thead>
              <tbody>
                {state.players.map((player) => (
                  <tr
                    key={player.id}
                    style={{ borderBottom: '1px solid #eee' }}
                  >
                    <td style={{ padding: '0.5rem' }}>{player.name}</td>
                    <td style={{ padding: '0.5rem' }}>{player.position}</td>
                    <td style={{ padding: '0.5rem' }}>{player.nflTeam || '-'}</td>
                    <td style={{ padding: '0.5rem', textAlign: 'right' }}>
                      {player.adp ? player.adp.toFixed(1) : '-'}
                    </td>
                    <td style={{ padding: '0.5rem', textAlign: 'center' }}>
                      <button
                        onClick={() => handlePick(player.id)}
                        disabled={state.currentPick > totalPicks}
                        style={{
                          padding: '0.25rem 0.75rem',
                          background:
                            state.currentPick <= totalPicks ? '#0070f3' : '#ccc',
                          color: 'white',
                          border: 'none',
                          borderRadius: '4px',
                          cursor:
                            state.currentPick <= totalPicks
                              ? 'pointer'
                              : 'not-allowed',
                        }}
                      >
                        Draft
                      </button>
                    </td>
                  </tr>
                ))}
                {state.players.length === 0 && (
                  <tr>
                    <td
                      colSpan={5}
                      style={{ padding: '2rem', textAlign: 'center' }}
                    >
                      No available players
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </section>

        {/* Draft Queue Panel */}
        <section>
          <h2>Draft Queue</h2>
          <div
            style={{
              border: '1px solid #ddd',
              borderRadius: '8px',
              maxHeight: '500px',
              overflow: 'auto',
            }}
          >
            <table style={{ width: '100%', borderCollapse: 'collapse' }}>
              <thead style={{ position: 'sticky', top: 0, background: '#f5f5f5' }}>
                <tr>
                  <th style={{ padding: '0.5rem', textAlign: 'center' }}>#</th>
                  <th style={{ padding: '0.5rem', textAlign: 'left' }}>Rd</th>
                  <th style={{ padding: '0.5rem', textAlign: 'left' }}>Team</th>
                  <th style={{ padding: '0.5rem', textAlign: 'left' }}>Player</th>
                </tr>
              </thead>
              <tbody>
                {state.picks.map((pick) => {
                  const player = state.players.find(
                    (pl) => pl.id === pick.selectedPlayerId
                  );
                  const isCurrentPick = pick.overallPick === state.currentPick;

                  return (
                    <tr
                      key={pick.overallPick}
                      style={{
                        borderBottom: '1px solid #eee',
                        background: isCurrentPick ? '#e6f3ff' : 'transparent',
                      }}
                    >
                      <td
                        style={{
                          padding: '0.5rem',
                          textAlign: 'center',
                          fontWeight: isCurrentPick ? 'bold' : 'normal',
                        }}
                      >
                        {pick.overallPick}
                      </td>
                      <td style={{ padding: '0.5rem' }}>{pick.round}</td>
                      <td style={{ padding: '0.5rem' }}>
                        {pick.currentOwnerTeamId}
                      </td>
                      <td style={{ padding: '0.5rem' }}>
                        {player ? (
                          <span>
                            {player.name} ({player.position})
                          </span>
                        ) : (
                          <span style={{ color: '#999' }}>-</span>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </section>
      </div>

      <footer style={{ marginTop: '2rem', padding: '1rem', background: '#f5f5f5', borderRadius: '8px' }}>
        <p style={{ margin: 0, fontSize: '0.875rem', color: '#666' }}>
          <strong>Phase 4 Note:</strong> Player data is loaded from the Prisma-backed catalog.
          CSV import is available at <code>/api/players/import</code>.
        </p>
      </footer>
    </div>
  );
}
