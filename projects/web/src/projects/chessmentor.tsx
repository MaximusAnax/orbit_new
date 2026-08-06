/**
 * ChessMentor — play an opponent calibrated to your level, then find out why you
 * lost in words you can act on.
 *
 * The product's premise is an opponent that is beatable but not stupid, so the
 * UI keeps the calibration visible: which level you're facing, what that level's
 * rating is, and whether it was chosen for you or overridden.
 *
 * chess.js runs only in the browser, for move input and legality highlighting.
 * The server stays authoritative — every move is sent to it, and its returned
 * position is what gets rendered.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { Chess } from "chess.js";
import { ApiError, client } from "../lib/api";
import { useMutation, useQuery } from "../lib/hooks";
import { Async, Button, ErrorNote, Facts, Page, Panel, Pill, Stat, StatRow, State } from "../ui/kit";
import "./chessmentor.css";

const api = client("chessmentor");

type Game = {
  id: number;
  player_color: "white" | "black";
  level_id: number;
  level_elo: number;
  recommended_level_id: number;
  level_overridden: boolean;
  status: string;
  termination?: string | null;
  result_score?: number | null;
  ply_count: number;
  final_fen: string;
  opening_name?: string | null;
};
type MoveRecord = { ply: number; color: string; san: string; uci: string };
type GameState = {
  game: Game;
  fen: string;
  turn: "white" | "black";
  player_to_move: boolean;
  legal_moves_san: string[];
  moves: MoveRecord[];
  pgn: string;
};
type MoveResponse = {
  game: Game;
  player_move: MoveRecord | null;
  cpu_move: MoveRecord | null;
  fen: string;
  legal_moves_san: string[];
};
type Rating = {
  state: {
    glicko_rating: number;
    glicko_rd: number;
    rated_games: number;
    judged_games: number;
    current_level_id: number;
    calibration_warning: boolean;
  };
  r_hat: number;
  expected_score_at_current_level: number;
  recommended_level_id: number;
};

const FILES = ["a", "b", "c", "d", "e", "f", "g", "h"];
const GLYPH: Record<string, string> = {
  p: "♟", n: "♞", b: "♝", r: "♜", q: "♛", k: "♚",
  P: "♙", N: "♘", B: "♗", R: "♖", Q: "♕", K: "♔",
};

/** Squares in render order for the given orientation. */
function squares(flipped: boolean): string[] {
  const ranks = flipped ? [1, 2, 3, 4, 5, 6, 7, 8] : [8, 7, 6, 5, 4, 3, 2, 1];
  const files = flipped ? [...FILES].reverse() : FILES;
  return ranks.flatMap((rank) => files.map((file) => `${file}${rank}`));
}

function Board({
  fen,
  flipped,
  selected,
  targets,
  lastMove,
  onSquare,
  disabled,
}: {
  fen: string;
  flipped: boolean;
  selected: string | null;
  targets: string[];
  lastMove: { from: string; to: string } | null;
  onSquare: (square: string) => void;
  disabled: boolean;
}) {
  const position = useMemo(() => {
    const map: Record<string, string> = {};
    const board = fen.split(" ")[0];
    board.split("/").forEach((row, rankIndex) => {
      let fileIndex = 0;
      for (const ch of row) {
        if (/\d/.test(ch)) {
          fileIndex += Number(ch);
        } else {
          map[`${FILES[fileIndex]}${8 - rankIndex}`] = ch;
          fileIndex += 1;
        }
      }
    });
    return map;
  }, [fen]);

  return (
    <div className={`board${disabled ? " thinking" : ""}`} role="grid" aria-label="Chess board">
      {squares(flipped).map((square) => {
        const piece = position[square];
        const file = FILES.indexOf(square[0]);
        const rank = Number(square[1]);
        const dark = (file + rank) % 2 === 0;
        const isTarget = targets.includes(square);
        return (
          <button
            key={square}
            data-square={square}
            className={[
              "square",
              dark ? "dark" : "light",
              selected === square ? "selected" : "",
              isTarget ? (piece ? "capture" : "target") : "",
              lastMove && (lastMove.from === square || lastMove.to === square) ? "last" : "",
            ]
              .filter(Boolean)
              .join(" ")}
            onClick={() => onSquare(square)}
            disabled={disabled}
            aria-label={`${square}${piece ? ` ${piece}` : " empty"}`}
          >
            {piece ? (
              <span className={`piece ${piece === piece.toUpperCase() ? "white" : "black"}`}>
                {GLYPH[piece]}
              </span>
            ) : null}
            {square[1] === (flipped ? "8" : "1") ? (
              <span className="coord file" aria-hidden="true">{square[0]}</span>
            ) : null}
            {square[0] === (flipped ? "h" : "a") ? (
              <span className="coord rank" aria-hidden="true">{square[1]}</span>
            ) : null}
          </button>
        );
      })}
    </div>
  );
}

function MoveList({ moves }: { moves: MoveRecord[] }) {
  const pairs: { n: number; white?: MoveRecord; black?: MoveRecord }[] = [];
  moves.forEach((m) => {
    const n = Math.ceil(m.ply / 2);
    let entry = pairs.find((p) => p.n === n);
    if (!entry) {
      entry = { n };
      pairs.push(entry);
    }
    if (m.color === "white") entry.white = m;
    else entry.black = m;
  });

  if (!moves.length) return <p className="move-empty">No moves yet.</p>;

  return (
    <ol className="move-list">
      {pairs.map((p) => (
        <li key={p.n}>
          <span className="move-no num">{p.n}.</span>
          <span className="move mono">{p.white?.san ?? "…"}</span>
          <span className="move mono">{p.black?.san ?? ""}</span>
        </li>
      ))}
    </ol>
  );
}

function ActiveGame({ gameId, onFinished }: { gameId: number; onFinished: () => void }) {
  const state = useQuery(() => api.get<GameState>(`/games/${gameId}`), [gameId]);
  const [selected, setSelected] = useState<string | null>(null);
  const [targets, setTargets] = useState<string[]>([]);
  const [lastMove, setLastMove] = useState<{ from: string; to: string } | null>(null);
  const [live, setLive] = useState<Partial<GameState> | null>(null);

  const move = useMutation((uci: string) =>
    api.post<MoveResponse>(`/games/${gameId}/moves`, { move: uci }),
  );

  const fen = live?.fen ?? state.data?.fen ?? "";
  const game = (live?.game ?? state.data?.game) as Game | undefined;
  const moves = (live?.moves ?? state.data?.moves ?? []) as MoveRecord[];
  const flipped = game?.player_color === "black";
  const finished = game && game.status !== "in_progress";

  useEffect(() => {
    if (finished) onFinished();
  }, [finished, onFinished]);

  const clickSquare = useCallback(
    async (square: string) => {
      if (!fen || move.pending || finished) return;
      const chess = new Chess(fen);

      if (selected) {
        // Second click: try the move. Promotion always to queen for simplicity.
        const legal = chess.moves({ square: selected as never, verbose: true }) as {
          to: string;
          promotion?: string;
        }[];
        const target = legal.find((m) => m.to === square);
        if (target) {
          const uci = `${selected}${square}${target.promotion ? "q" : ""}`;
          setSelected(null);
          setTargets([]);
          const result = await move.run(uci);
          if (result) {
            setLastMove(
              result.cpu_move
                ? { from: result.cpu_move.uci.slice(0, 2), to: result.cpu_move.uci.slice(2, 4) }
                : { from: selected, to: square },
            );
            const played = [result.player_move, result.cpu_move].filter(Boolean) as MoveRecord[];
            setLive({
              fen: result.fen,
              game: result.game,
              moves: [...moves, ...played],
              legal_moves_san: result.legal_moves_san,
            });
          }
          return;
        }
      }

      // First click (or reselect): show this piece's destinations.
      const piece = chess.get(square as never);
      const myColour = game?.player_color === "black" ? "b" : "w";
      if (piece && piece.color === myColour) {
        setSelected(square);
        setTargets(
          (chess.moves({ square: square as never, verbose: true }) as { to: string }[]).map((m) => m.to),
        );
      } else {
        setSelected(null);
        setTargets([]);
      }
    },
    [fen, selected, move, moves, game, finished],
  );

  return (
    <Async query={state}>
      {(initial) => {
        const g = game ?? initial.game;
        const outcome =
          g.status !== "in_progress"
            ? g.result_score === 1
              ? "You won"
              : g.result_score === 0
                ? "You lost"
                : "Drawn"
            : null;

        return (
          <div className="chess-layout">
            <div className="board-side">
              <Board
                fen={fen || initial.fen}
                flipped={flipped}
                selected={selected}
                targets={targets}
                lastMove={lastMove}
                onSquare={clickSquare}
                disabled={move.pending || Boolean(finished)}
              />
              <div className="board-status">
                {move.pending ? (
                  <span className="thinking-note">
                    <span className="spinner spinner-sm" aria-hidden="true" /> L{g.level_id} is
                    thinking…
                  </span>
                ) : outcome ? (
                  <Pill tone={g.result_score === 1 ? "ok" : g.result_score === 0 ? "bad" : "neutral"}>
                    {outcome}
                    {g.termination ? ` — ${g.termination}` : ""}
                  </Pill>
                ) : (
                  <span className="turn-note">
                    You are {g.player_color}. {selected ? "Choose a destination." : "Click a piece."}
                  </span>
                )}
              </div>
              <ErrorNote error={move.error} />
            </div>

            <div className="side-panels">
              <Panel title={`Level ${g.level_id}`} hint={`about ${Math.round(g.level_elo)} Elo`}>
                <Facts
                  items={[
                    [
                      "Chosen",
                      g.level_overridden
                        ? `you overrode the suggested L${g.recommended_level_id}`
                        : "matched to your rating",
                    ],
                    ["Moves played", <span className="num">{g.ply_count}</span>],
                    ...(g.opening_name ? [["Opening", g.opening_name] as [string, string]] : []),
                  ]}
                />
              </Panel>

              <Panel title="Moves" padded={false}>
                <div className="move-wrap">
                  <MoveList moves={moves.length ? moves : initial.moves} />
                </div>
              </Panel>
            </div>
          </div>
        );
      }}
    </Async>
  );
}

export default function ChessMentor() {
  const [gameId, setGameId] = useState<number | null>(null);
  const [finishedAt, setFinishedAt] = useState(0);
  const rating = useQuery(() => api.get<Rating>("/rating"), [finishedAt]);

  const start = useMutation(async (color?: string) => {
    try {
      const created = await api.post<{ game: Game }>("/games", color ? { player_color: color } : {});
      return created.game;
    } catch (err) {
      // One game at a time is a rule of the product, not a failure — resume it.
      if (err instanceof ApiError && err.code === "game_in_progress") {
        const detail = err.detail as { in_progress_game_id?: number } | undefined;
        const existing = detail?.in_progress_game_id;
        if (existing) return { id: existing } as Game;
      }
      throw err;
    }
  });

  // Resume an in-progress game on arrival so the board is never empty.
  const existing = useQuery(
    () => api.get<{ games: Game[] } | Game[]>("/games"),
    [finishedAt],
  );

  useEffect(() => {
    if (gameId || !existing.data) return;
    const list = Array.isArray(existing.data) ? existing.data : existing.data.games;
    const open = list?.find((g) => g.status === "in_progress");
    if (open) setGameId(open.id);
  }, [existing.data, gameId]);

  const begin = async (color?: string) => {
    const game = await start.run(color);
    if (game) setGameId(game.id);
  };

  return (
    <Page
      title="ChessMentor"
      lede="An opponent calibrated to be beatable but not stupid — and a coach that tells you what to fix in words you can act on."
      actions={
        gameId ? (
          <Button
            onClick={() => {
              setGameId(null);
              setFinishedAt(Date.now());
            }}
          >
            New game
          </Button>
        ) : null
      }
    >
      {rating.data ? (
        <StatRow>
          <Stat
            label="Your rating"
            value={Math.round(rating.data.r_hat)}
            sub={`± ${Math.round(rating.data.state.glicko_rd)} uncertainty`}
          />
          <Stat
            label="Rated games"
            value={rating.data.state.rated_games}
            sub={
              rating.data.state.rated_games === 0
                ? "rating is still a guess"
                : `${rating.data.state.judged_games} judged`
            }
          />
          <Stat
            label="Suggested level"
            value={`L${rating.data.recommended_level_id}`}
            sub={rating.data.state.calibration_warning ? "calibration drifting" : "calibrated"}
            tone={rating.data.state.calibration_warning ? "warn" : undefined}
          />
          {/* The product's whole claim, in one number: the matched level should
              give you roughly an even game. */}
          <Stat
            label="Your expected score"
            value={`${Math.round(rating.data.expected_score_at_current_level * 100)}%`}
            sub={`against L${rating.data.state.current_level_id}`}
          />
        </StatRow>
      ) : null}

      {gameId ? (
        <ActiveGame gameId={gameId} onFinished={() => setFinishedAt(Date.now())} />
      ) : (
        <Panel title="Start a game">
          <p className="lede">
            The level is picked from your rating. Play as either colour, or let it choose.
          </p>
          <div className="start-row">
            <Button variant="primary" pending={start.pending} onClick={() => void begin("white")}>
              Play as white
            </Button>
            <Button pending={start.pending} onClick={() => void begin("black")}>
              Play as black
            </Button>
            <Button variant="ghost" pending={start.pending} onClick={() => void begin()}>
              Surprise me
            </Button>
          </div>
          <ErrorNote error={start.error} />
          {existing.loading ? <State kind="loading" title="Checking for a game in progress…" /> : null}
        </Panel>
      )}
    </Page>
  );
}
