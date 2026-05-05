from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import itertools
import os
import math
import secrets
from typing import Optional

from flask import Flask, render_template, request, redirect, url_for, abort, jsonify
from flask_socketio import SocketIO, join_room, emit
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "CHANGE_ME_DEV_SECRET")
database_url = os.environ.get("DATABASE_URL", "sqlite:///padel.db")
if database_url.startswith("postgres://"):
    database_url = database_url.replace("postgres://", "postgresql://", 1)
app.config["SQLALCHEMY_DATABASE_URI"] = database_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = "login"
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

POINT_LABELS = ["0", "15", "30", "40"]
SCORING_MODES = {
    "golden_point": "Golden Point",
    "advantage": "Vorteil / Einstand",
    "star_point_2026": "Star Point 2026",
}


class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    dark_mode = db.Column(db.Boolean, nullable=False, default=False)


class Tournament(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    public_id = db.Column(db.String(16), unique=True, nullable=False, default=lambda: secrets.token_urlsafe(8))
    name = db.Column(db.String(120), nullable=False)
    status = db.Column(db.String(20), nullable=False, default="created")
    owner_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    courts_count = db.Column(db.Integer, nullable=False, default=2)
    double_round = db.Column(db.Boolean, nullable=False, default=False)
    round_minutes = db.Column(db.Integer, nullable=False, default=20)
    warning_seconds = db.Column(db.Integer, nullable=False, default=120)
    scoring_mode = db.Column(db.String(30), nullable=False, default="golden_point")
    active_round = db.Column(db.Integer, nullable=False, default=1)
    round_started_at = db.Column(db.DateTime(timezone=True), nullable=True)
    round_ends_at = db.Column(db.DateTime(timezone=True), nullable=True)
    timer_running = db.Column(db.Boolean, nullable=False, default=False)
    warning_announced = db.Column(db.Boolean, nullable=False, default=False)
    paused_remaining_seconds = db.Column(db.Integer, nullable=True)
    dark_mode = db.Column(db.Boolean, nullable=False, default=False)


class Court(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    tournament_id = db.Column(db.Integer, db.ForeignKey("tournament.id"), nullable=False)
    number = db.Column(db.Integer, nullable=False, default=1)
    name = db.Column(db.String(80), nullable=False, default="Court")
    controller_token = db.Column(db.String(32), unique=True, nullable=False, default=lambda: secrets.token_urlsafe(18))
    timer_running = db.Column(db.Boolean, nullable=False, default=False)
    round_started_at = db.Column(db.DateTime(timezone=True), nullable=True)
    round_ends_at = db.Column(db.DateTime(timezone=True), nullable=True)
    paused_remaining_seconds = db.Column(db.Integer, nullable=True)
    warning_announced = db.Column(db.Boolean, nullable=False, default=False)


class Player(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    tournament_id = db.Column(db.Integer, db.ForeignKey("tournament.id"), nullable=False)
    name = db.Column(db.String(80), nullable=False)


class Team(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    tournament_id = db.Column(db.Integer, db.ForeignKey("tournament.id"), nullable=False)
    name = db.Column(db.String(80), nullable=False)
    player1_id = db.Column(db.Integer, db.ForeignKey("player.id"), nullable=True)
    player2_id = db.Column(db.Integer, db.ForeignKey("player.id"), nullable=True)


class Match(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    tournament_id = db.Column(db.Integer, db.ForeignKey("tournament.id"), nullable=False)
    team1_id = db.Column(db.Integer, db.ForeignKey("team.id"), nullable=False)
    team2_id = db.Column(db.Integer, db.ForeignKey("team.id"), nullable=False)
    score1 = db.Column(db.Integer, nullable=True)
    score2 = db.Column(db.Integer, nullable=True)
    set_score1 = db.Column(db.Integer, nullable=False, default=0)
    set_score2 = db.Column(db.Integer, nullable=False, default=0)
    round_no = db.Column(db.Integer, nullable=False, default=1)
    court_id = db.Column(db.Integer, db.ForeignKey("court.id"), nullable=True)
    phase = db.Column(db.String(20), nullable=False, default="group")
    status = db.Column(db.String(20), nullable=False, default="scheduled")
    points1 = db.Column(db.Integer, nullable=False, default=0)
    points2 = db.Column(db.Integer, nullable=False, default=0)
    games1 = db.Column(db.Integer, nullable=False, default=0)
    games2 = db.Column(db.Integer, nullable=False, default=0)
    sets1 = db.Column(db.Integer, nullable=False, default=0)
    sets2 = db.Column(db.Integer, nullable=False, default=0)
    scoring_mode = db.Column(db.String(30), nullable=False, default="golden_point")
    history = db.Column(db.Text, nullable=False, default="")
    started_at = db.Column(db.DateTime(timezone=True), nullable=True)
    finished_at = db.Column(db.DateTime(timezone=True), nullable=True)
    serving_team = db.Column(db.Integer, nullable=False, default=0)
    team1_server_player = db.Column(db.Integer, nullable=False, default=0)
    team2_server_player = db.Column(db.Integer, nullable=False, default=0)
    team1_has_served = db.Column(db.Boolean, nullable=False, default=True)
    team2_has_served = db.Column(db.Boolean, nullable=False, default=False)


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))


def now_utc():
    return datetime.utcnow()


def current_user_owns(tournament):
    return current_user.is_authenticated and tournament.owner_id == current_user.id


def get_tournament_or_404(public_id):
    t = Tournament.query.filter_by(public_id=public_id).first()
    if not t:
        abort(404)
    return t


def require_owner(t):
    if not current_user_owns(t):
        abort(403)


def get_players(tid):
    return Player.query.filter_by(tournament_id=tid).order_by(Player.id).all()


def get_teams(tid):
    return Team.query.filter_by(tournament_id=tid).order_by(Team.id).all()


def get_courts(tid):
    return Court.query.filter_by(tournament_id=tid).order_by(Court.number, Court.id).all()


def get_matches(tid, phase=None):
    q = Match.query.filter_by(tournament_id=tid)
    if phase:
        q = q.filter_by(phase=phase)
    return q.order_by(Match.round_no, Match.court_id, Match.id).all()


def get_round_matches(tid, round_no):
    return Match.query.filter_by(tournament_id=tid, round_no=round_no).order_by(Match.court_id, Match.id).all()


def serialize_history(m: Match):
    return m.history.split("|") if m.history else []


def push_history(m: Match):
    snap = f"{m.points1},{m.points2},{m.games1},{m.games2},{m.sets1},{m.sets2},{m.status},{m.serving_team},{m.team1_server_player},{m.team2_server_player},{int(m.team1_has_served)},{int(m.team2_has_served)}"
    items = serialize_history(m)
    items.append(snap)
    m.history = "|".join(items[-80:])


def undo_match(m: Match):
    items = serialize_history(m)
    if not items:
        return
    snap = items.pop().split(",")
    m.points1, m.points2, m.games1, m.games2, m.sets1, m.sets2 = map(int, snap[:6])
    m.status = snap[6]
    if len(snap) >= 12:
        m.serving_team = int(snap[7])
        m.team1_server_player = int(snap[8])
        m.team2_server_player = int(snap[9])
        m.team1_has_served = bool(int(snap[10]))
        m.team2_has_served = bool(int(snap[11]))
    m.score1 = m.games1
    m.score2 = m.games2
    m.set_score1 = m.sets1
    m.set_score2 = m.sets2
    m.finished_at = None if m.status != "finished" else m.finished_at
    m.history = "|".join(items)


def in_tiebreak(m: Match):
    return m.games1 == 6 and m.games2 == 6


def display_points(m: Match):
    a, b = m.points1, m.points2
    mode = m.scoring_mode or "golden_point"
    if in_tiebreak(m):
        return f"Tiebreak {a} : {b}"
    if mode == "advantage" and a >= 3 and b >= 3:
        if a == b:
            return "40 : 40"
        return "Vorteil links" if a > b else "Vorteil rechts"
    if mode in ["golden_point", "star_point_2026"] and a >= 3 and b >= 3:
        if a == b:
            return "40 : 40"
    return f"{POINT_LABELS[min(a, 3)]} : {POINT_LABELS[min(b, 3)]}"


def _normal_service_team(m: Match, completed_games: Optional[int] = None):
    """Reguläre Padel-Aufschlagreihenfolge.

    Ein Spieler serviert immer ein komplettes Game. Danach wechselt das
    aufschlagende Team. Wenn ein Team wieder an der Reihe ist, serviert der
    andere Spieler dieses Teams. Bei 6:6 wird die Tiebreak-Reihenfolge genutzt.
    """
    if completed_games is None:
        completed_games = (m.games1 or 0) + (m.games2 or 0)
    start_team = 0 if (m.serving_team or 0) == 0 else 1
    return (start_team + completed_games) % 2


def _normal_service_count_for_team(m: Match, team_idx: int, completed_games: Optional[int] = None):
    if completed_games is None:
        completed_games = (m.games1 or 0) + (m.games2 or 0)
    start_team = 0 if (m.serving_team or 0) == 0 else 1
    count = 0
    for game_no in range(completed_games):
        if (start_team + game_no) % 2 == team_idx:
            count += 1
    return count


def _tiebreak_service_turn(total_tb_points: int):
    # Vor dem 1. Punkt: Turn 0 = 1 Aufschlag. Danach je 2 Aufschläge pro Turn.
    if total_tb_points <= 0:
        return 0
    return 1 + ((total_tb_points - 1) // 2)


def serving_team_index(m: Match):
    if in_tiebreak(m):
        tb_turn = _tiebreak_service_turn((m.points1 or 0) + (m.points2 or 0))
        start_team = _normal_service_team(m, completed_games=(m.games1 or 0) + (m.games2 or 0))
        return (start_team + tb_turn) % 2
    return _normal_service_team(m)


def serving_player_slot(m: Match):
    idx = serving_team_index(m)
    completed_games = (m.games1 or 0) + (m.games2 or 0)
    base_count = _normal_service_count_for_team(m, idx, completed_games)

    if in_tiebreak(m):
        total_tb_points = (m.points1 or 0) + (m.points2 or 0)
        current_turn = _tiebreak_service_turn(total_tb_points)
        turns_for_team_before_current = 0
        start_team = _normal_service_team(m, completed_games=completed_games)
        for turn in range(current_turn):
            if (start_team + turn) % 2 == idx:
                turns_for_team_before_current += 1
        return (base_count + turns_for_team_before_current) % 2

    return base_count % 2


def serving_player_name(m: Match):
    idx = serving_team_index(m)
    team = db.session.get(Team, m.team1_id if idx == 0 else m.team2_id)
    if not team:
        return ""
    player_id = team.player1_id if serving_player_slot(m) == 0 else team.player2_id
    player = db.session.get(Player, player_id) if player_id else None
    return player.name if player else ("Spieler 1" if serving_player_slot(m) == 0 else "Spieler 2")


def serving_team_name(m: Match):
    idx = serving_team_index(m)
    team = db.session.get(Team, m.team1_id if idx == 0 else m.team2_id)
    return team.name if team else ("links" if idx == 0 else "rechts")


def serving_display_name(m: Match):
    team = serving_team_name(m)
    player = serving_player_name(m)
    return f"{team} · {player}" if player else team


def is_final_phase(m: Optional[Match]):
    return bool(m and m.phase in ["final", "third", "semifinal"])


def game_to_six(value):
    return 6 if value > 0 else 0


def finish_match(m: Match, reason="manual"):
    if m.status == "finished":
        return
    # Bei Zeitrunden wird erst beim Rundenende gewertet. Ein laufendes Game wird dann fair
    # als Game für die führende Seite übernommen; bei Gleichstand bleibt es ungewertet.
    if m.points1 != m.points2:
        winner = 0 if m.points1 > m.points2 else 1
        win_game(m, winner, finish_check=False)
    m.status = "finished"
    m.finished_at = now_utc()
    # Ein gewonnener Satz zählt in der Wertung als 6 Games. Danach begonnene Games kommen dazu.
    m.score1 = (m.sets1 * 6) + m.games1
    m.score2 = (m.sets2 * 6) + m.games2
    m.set_score1 = m.sets1
    m.set_score2 = m.sets2


def win_set(m: Match, team_idx: int):
    # Nach Satzende läuft die reguläre Aufschlagreihenfolge weiter. Weil Games
    # für den neuen Satz auf 0 gesetzt werden, speichern wir vorher das Team,
    # das im nächsten Game als erstes servieren muss, als neuen Satz-Startserver.
    completed_games = (m.games1 or 0) + (m.games2 or 0)
    next_set_start_server = _normal_service_team(m, completed_games=completed_games)
    if team_idx == 0:
        m.sets1 += 1
    else:
        m.sets2 += 1
    m.serving_team = next_set_start_server
    m.games1 = 0
    m.games2 = 0
    m.points1 = 0
    m.points2 = 0
    m.set_score1 = m.sets1
    m.set_score2 = m.sets2
    # Wichtig: Ein Satzgewinn beendet Zeitrunden NICHT automatisch. S/U/N entsteht erst
    # beim Rundenabschluss. Finalrunden werden ebenfalls nur manuell beendet.


def win_game(m: Match, team_idx: int, finish_check=True):
    if team_idx == 0:
        m.games1 += 1
    else:
        m.games2 += 1
    m.points1 = 0
    m.points2 = 0
    if finish_check:
        g1, g2 = m.games1, m.games2
        if g1 >= 6 and g1 - g2 >= 2:
            win_set(m, 0)
        elif g2 >= 6 and g2 - g1 >= 2:
            win_set(m, 1)
        # Bei 6:6 startet ein Tiebreak. Der Satz endet erst, wenn der Tiebreak
        # mit mindestens 7 Punkten und 2 Punkten Abstand gewonnen ist.
    m.score1 = m.games1
    m.score2 = m.games2
    m.set_score1 = m.sets1
    m.set_score2 = m.sets2


def add_point(m: Match, team_idx: int):
    if m.status == "finished":
        return
    if m.status == "scheduled":
        m.status = "running"
        m.started_at = now_utc()
    push_history(m)
    if team_idx == 0:
        m.points1 += 1
    else:
        m.points2 += 1
    a, b = m.points1, m.points2

    if in_tiebreak(m):
        # Tiebreak bis mindestens 7, mit 2 Punkten Abstand. Ergebnis wird als 7:6-Game
        # im Satz geführt und danach als Satzgewinn gespeichert.
        if a >= 7 and a - b >= 2:
            m.games1 = 7
            win_set(m, 0)
        elif b >= 7 and b - a >= 2:
            m.games2 = 7
            win_set(m, 1)
        m.score1 = m.games1
        m.score2 = m.games2
        return

    mode = m.scoring_mode or "golden_point"
    if mode == "advantage":
        if a >= 4 and a - b >= 2:
            win_game(m, 0)
        elif b >= 4 and b - a >= 2:
            win_game(m, 1)
    else:
        if team_idx == 0 and a >= 4 and b >= 3:
            win_game(m, 0)
        elif team_idx == 1 and b >= 4 and a >= 3:
            win_game(m, 1)
        elif a >= 4 and b < 3:
            win_game(m, 0)
        elif b >= 4 and a < 3:
            win_game(m, 1)
    m.score1 = m.games1
    m.score2 = m.games2


def reset_score(m: Match):
    push_history(m)
    m.points1 = m.points2 = m.games1 = m.games2 = m.sets1 = m.sets2 = 0
    m.serving_team = 0
    m.team1_server_player = 0
    m.team2_server_player = 0
    m.team1_has_served = True
    m.team2_has_served = False
    m.score1 = m.score2 = 0
    m.set_score1 = m.set_score2 = 0
    m.status = "scheduled"
    m.started_at = None
    m.finished_at = None


def set_manual_score(m: Match, score1: int, score2: int):
    push_history(m)
    score1 = max(0, int(score1))
    score2 = max(0, int(score2))
    m.score1 = score1
    m.score2 = score2
    # Für Live-Anzeige übernehmen wir den manuellen Endstand als Game-Anzeige.
    # Satzwertung bleibt separat, falls ein Match vorher per Padel-Zählung einen Satz erzeugt hat.
    m.games1 = score1
    m.games2 = score2
    m.points1 = 0
    m.points2 = 0
    m.status = "finished"
    if not m.started_at:
        m.started_at = now_utc()
    m.finished_at = now_utc()


def reset_tournament_to_round_one(t: Tournament):
    for m in get_matches(t.id):
        m.points1 = m.points2 = 0
        m.games1 = m.games2 = 0
        m.sets1 = m.sets2 = 0
        m.score1 = m.score2 = 0
        m.set_score1 = m.set_score2 = 0
        m.status = "scheduled"
        m.history = ""
        m.started_at = None
        m.finished_at = None
        m.scoring_mode = t.scoring_mode
    t.active_round = 1
    t.status = "created"
    t.timer_running = False
    t.round_started_at = None
    t.round_ends_at = None
    t.paused_remaining_seconds = None
    t.warning_announced = False
    assign_courts_for_round(t)


def delete_tournament(t: Tournament):
    Match.query.filter_by(tournament_id=t.id).delete()
    Team.query.filter_by(tournament_id=t.id).delete()
    Player.query.filter_by(tournament_id=t.id).delete()
    Court.query.filter_by(tournament_id=t.id).delete()
    db.session.delete(t)


def ensure_courts(t: Tournament, names=None):
    names = names or [f"Court {i+1}" for i in range(t.courts_count)]
    existing = get_courts(t.id)
    for i, name in enumerate(names, start=1):
        if i <= len(existing):
            existing[i-1].name = name or f"Court {i}"
            existing[i-1].number = i
        else:
            db.session.add(Court(tournament_id=t.id, number=i, name=name or f"Court {i}"))
    for c in existing[len(names):]:
        db.session.delete(c)
    t.courts_count = len(names)
    db.session.commit()


def assign_courts_for_round(t: Tournament):
    courts = get_courts(t.id)
    matches = get_round_matches(t.id, t.active_round)
    for i, m in enumerate(matches):
        if i < len(courts):
            m.court_id = courts[i].id
            if m.status == "scheduled":
                m.scoring_mode = t.scoring_mode
        else:
            m.court_id = None
    db.session.commit()


def schedule_pairs_without_parallel_teams(pairs, court_count):
    """Packt Matches in Runden, ohne dass ein Team in einer Runde doppelt vorkommt."""
    unscheduled = list(pairs)
    rounds = []
    while unscheduled:
        used = set()
        current = []
        remaining = []
        for p1, p2 in unscheduled:
            if len(current) < court_count and p1.id not in used and p2.id not in used:
                current.append((p1, p2))
                used.add(p1.id)
                used.add(p2.id)
            else:
                remaining.append((p1, p2))
        # Sicherheitsfallback: Falls durch eine ungünstige Reihenfolge nichts passt,
        # nimm genau ein Match. Dadurch bleibt die Schleife garantiert endlich.
        if not current and remaining:
            p1, p2 = remaining.pop(0)
            current.append((p1, p2))
        rounds.append(current)
        unscheduled = remaining
    return rounds


def generate_group_matches(tournament: Tournament):
    teams = get_teams(tournament.id)
    pairs = list(itertools.combinations(teams, 2))
    if tournament.double_round:
        pairs += [(b, a) for a, b in pairs]

    Match.query.filter_by(tournament_id=tournament.id).delete()
    db.session.commit()

    scheduled_rounds = schedule_pairs_without_parallel_teams(pairs, max(1, tournament.courts_count))
    courts = get_courts(tournament.id)
    for round_no, round_pairs in enumerate(scheduled_rounds, start=1):
        for court_index, (p1, p2) in enumerate(round_pairs):
            db.session.add(Match(
                tournament_id=tournament.id,
                team1_id=p1.id,
                team2_id=p2.id,
                round_no=round_no,
                court_id=courts[court_index].id if court_index < len(courts) else None,
                phase="group",
                scoring_mode=tournament.scoring_mode,
                score1=0,
                score2=0,
            ))
    tournament.active_round = 1
    tournament.timer_running = False
    tournament.paused_remaining_seconds = None
    tournament.round_started_at = None
    tournament.round_ends_at = None
    tournament.warning_announced = False
    db.session.commit()
    assign_courts_for_round(tournament)


def create_finals_if_possible(t: Tournament):
    if Match.query.filter_by(tournament_id=t.id, phase="final").first():
        return
    table = calculate_table(t.id)
    if len(table) < 2:
        return
    courts = get_courts(t.id)
    max_round = db.session.query(db.func.max(Match.round_no)).filter_by(tournament_id=t.id).scalar() or t.active_round
    final_round = max_round + 1
    db.session.add(Match(
        tournament_id=t.id,
        team1_id=table[0]["id"],
        team2_id=table[1]["id"],
        round_no=final_round,
        court_id=courts[0].id if courts else None,
        phase="final",
        scoring_mode=t.scoring_mode,
        score1=0,
        score2=0,
    ))
    if len(table) >= 4 and len(courts) >= 2:
        db.session.add(Match(
            tournament_id=t.id,
            team1_id=table[2]["id"],
            team2_id=table[3]["id"],
            round_no=final_round,
            court_id=courts[1].id,
            phase="third",
            scoring_mode=t.scoring_mode,
            score1=0,
            score2=0,
        ))
    db.session.commit()


def calculate_table(tournament_id):
    teams = get_teams(tournament_id)
    table = {
        t.id: {
            "id": t.id,
            "name": t.name,
            "players": team_players(t),
            "player_links": [{"id": p.id, "name": p.name} for p in [db.session.get(Player, t.player1_id), db.session.get(Player, t.player2_id)] if p],
            "points": 0,
            "played": 0,
            "wins": 0,
            "draws": 0,
            "losses": 0,
            "for": 0,
            "against": 0,
            "diff": 0,
            "sets_for": 0,
            "sets_against": 0,
            "winrate": 0,
        }
        for t in teams
    }
    for m in get_matches(tournament_id, phase="group"):
        if m.status != "finished":
            continue
        a = table.get(m.team1_id)
        b = table.get(m.team2_id)
        if not a or not b:
            continue
        s1 = m.score1 if m.score1 is not None else m.games1
        s2 = m.score2 if m.score2 is not None else m.games2
        a["played"] += 1; b["played"] += 1
        a["for"] += s1; a["against"] += s2
        b["for"] += s2; b["against"] += s1
        a["sets_for"] += m.set_score1; a["sets_against"] += m.set_score2
        b["sets_for"] += m.set_score2; b["sets_against"] += m.set_score1
        if s1 > s2:
            a["points"] += 3; a["wins"] += 1; b["losses"] += 1
        elif s2 > s1:
            b["points"] += 3; b["wins"] += 1; a["losses"] += 1
        else:
            a["points"] += 1; b["points"] += 1; a["draws"] += 1; b["draws"] += 1
    rows = []
    for row in table.values():
        row["diff"] = row["for"] - row["against"]
        row["winrate"] = round(row["wins"] / row["played"] * 100, 1) if row["played"] else 0
        rows.append(row)
    rows.sort(key=lambda x: (x["points"], x["diff"], x["for"], x["wins"]), reverse=True)
    return rows


def team_players(team: Team):
    names = []
    for pid in [team.player1_id, team.player2_id]:
        p = db.session.get(Player, pid) if pid else None
        if p:
            names.append(p.name)
    return names


def calculate_player_stats(player_id: int):
    player = db.session.get(Player, player_id)
    if not player:
        return None
    teams = [team for team in get_teams(player.tournament_id) if player_id in [team.player1_id, team.player2_id]]
    team_ids = {team.id for team in teams}
    base = {
        "id": player.id, "name": player.name, "teams": [team.name for team in teams],
        "points": 0, "played": 0, "wins": 0, "draws": 0, "losses": 0,
        "for": 0, "against": 0, "diff": 0, "sets_for": 0, "sets_against": 0, "winrate": 0,
    }
    for m in get_matches(player.tournament_id, phase="group"):
        if m.status != "finished" or (m.team1_id not in team_ids and m.team2_id not in team_ids):
            continue
        own_left = m.team1_id in team_ids
        own_score = (m.score1 or 0) if own_left else (m.score2 or 0)
        opp_score = (m.score2 or 0) if own_left else (m.score1 or 0)
        base["played"] += 1
        base["for"] += own_score
        base["against"] += opp_score
        base["sets_for"] += m.set_score1 if own_left else m.set_score2
        base["sets_against"] += m.set_score2 if own_left else m.set_score1
        if own_score > opp_score:
            base["points"] += 3; base["wins"] += 1
        elif own_score < opp_score:
            base["losses"] += 1
        else:
            base["points"] += 1; base["draws"] += 1
    base["diff"] = base["for"] - base["against"]
    base["winrate"] = round(base["wins"] / base["played"] * 100, 1) if base["played"] else 0
    return base


def bracket_data(t: Tournament):
    table = calculate_table(t.id)
    final = Match.query.filter_by(tournament_id=t.id, phase="final").first()
    third = Match.query.filter_by(tournament_id=t.id, phase="third").first()
    return {
        "ranking": table,
        "final": match_json(final) if final else None,
        "third": match_json(third) if third else None,
        "qualified": table[:4],
    }


def ai_coach(row, matches=None):
    if not row:
        return ["Noch keine Daten vorhanden."]
    tips = []
    if row["played"] < 2:
        tips.append("Noch wenig Daten: Spiele erst ein paar Matches, dann werden die Hinweise genauer.")
    if row["winrate"] >= 70:
        tips.append("Stärke: hohe Siegquote. Behalte die aggressive Netzposition bei und vermeide unnötiges Risiko bei klarer Führung.")
    elif row["winrate"] < 40 and row["played"] >= 2:
        tips.append("Verbesserung: weniger direkte Fehler. Spiele den ersten Ball sicher cross und rücke erst nach einem guten Lob oder tiefen Ball ans Netz.")
    if row["diff"] < 0:
        tips.append("Punktedifferenz negativ: trainiere Defensive, Lob aus der Ecke und ruhige Bande-Schläge, statt zu früh zu kontern.")
    elif row["diff"] > 8:
        tips.append("Punktedifferenz stark: du gewinnst viele Games deutlich. Arbeite weiter an Druck am Netz und klaren Calls mit deinem Partner.")
    if row["draws"] > 0:
        tips.append("Enge Spiele: übe Golden-Point-/40:40-Situationen mit klarer Absprache, wer die Mitte nimmt.")
    if row["against"] > row["for"]:
        tips.append("Viele Gegengames: Fokus auf Return-Quote und stabile erste Volleys, damit Gegner weniger leicht ans Netz kommen.")
    if not tips:
        tips.append("Solide Leistung: Nächster Fokus ist Konstanz über mehrere Runden und klare Kommunikation vor jedem Return.")
    return tips


def remaining_seconds(t: Tournament):
    # Globale Restzeit bleibt nur als Fallback/Anzeige erhalten. Die echte Laufzeit
    # wird ab dieser Version pro Court geführt.
    if t.timer_running and t.round_ends_at:
        rem = int((t.round_ends_at - now_utc()).total_seconds())
        return max(0, rem)
    if t.paused_remaining_seconds is not None:
        return max(0, int(t.paused_remaining_seconds))
    return t.round_minutes * 60


def court_remaining_seconds(court: Court, t: Tournament):
    if court.timer_running and court.round_ends_at:
        return max(0, int((court.round_ends_at - now_utc()).total_seconds()))
    if court.paused_remaining_seconds is not None:
        return max(0, int(court.paused_remaining_seconds))
    return t.round_minutes * 60


def court_has_final_match(court: Court, t: Tournament):
    m = Match.query.filter_by(tournament_id=t.id, round_no=t.active_round, court_id=court.id).first()
    return bool(m and is_final_phase(m))


def stop_all_court_timers(t: Tournament, reset=False):
    for c in get_courts(t.id):
        c.timer_running = False
        c.round_started_at = None
        c.round_ends_at = None
        c.warning_announced = False
        if reset:
            c.paused_remaining_seconds = None
    t.timer_running = False
    t.round_started_at = None
    t.round_ends_at = None
    t.warning_announced = False
    if reset:
        t.paused_remaining_seconds = None


def auto_finish_round_if_needed(t: Tournament):
    changed = False
    for court in get_courts(t.id):
        m = Match.query.filter_by(tournament_id=t.id, round_no=t.active_round, court_id=court.id).first()
        if not m or is_final_phase(m):
            continue
        if court.timer_running and court_remaining_seconds(court, t) <= 0:
            finish_match(m, reason="time")
            court.timer_running = False
            court.round_ends_at = None
            court.paused_remaining_seconds = 0
            changed = True
    if changed:
        t.status = "running"
        db.session.commit()


def start_court_timer(t: Tournament, court: Court):
    if court_has_final_match(court, t):
        court.timer_running = False
        court.round_started_at = None
        court.round_ends_at = None
        court.paused_remaining_seconds = None
        db.session.commit()
        return
    seconds = court.paused_remaining_seconds if court.paused_remaining_seconds is not None else t.round_minutes * 60
    court.timer_running = True
    court.warning_announced = False
    court.round_started_at = now_utc()
    court.round_ends_at = now_utc() + timedelta(seconds=max(0, int(seconds)))
    court.paused_remaining_seconds = None
    t.status = "running"
    db.session.commit()


def pause_court_timer(t: Tournament, court: Court):
    if court.timer_running:
        court.paused_remaining_seconds = court_remaining_seconds(court, t)
    court.timer_running = False
    court.round_ends_at = None
    db.session.commit()


def start_round(t: Tournament):
    # Admin-Start startet weiterhin alle Courts gleichzeitig. Im Controller werden
    # einzelne Courts über start_court_timer gestartet.
    for c in get_courts(t.id):
        start_court_timer(t, c)
    t.status = "running"
    db.session.commit()


def next_round(t: Tournament):
    active = get_round_matches(t.id, t.active_round)
    for m in active:
        finish_match(m, reason="manual")
    max_round = db.session.query(db.func.max(Match.round_no)).filter_by(tournament_id=t.id).scalar() or t.active_round
    if t.active_round >= max_round:
        create_finals_if_possible(t)
        max_round = db.session.query(db.func.max(Match.round_no)).filter_by(tournament_id=t.id).scalar() or t.active_round
    if t.active_round < max_round:
        t.active_round += 1
        assign_courts_for_round(t)
        stop_all_court_timers(t, reset=True)
    else:
        t.status = "finished"
        stop_all_court_timers(t, reset=True)
    db.session.commit()


def match_winner_name(m: Match):
    if m.status != "finished":
        return ""
    if (m.score1 or 0) > (m.score2 or 0):
        return db.session.get(Team, m.team1_id).name
    if (m.score2 or 0) > (m.score1 or 0):
        return db.session.get(Team, m.team2_id).name
    return "Unentschieden"


def match_json(m: Match):
    team1 = db.session.get(Team, m.team1_id)
    team2 = db.session.get(Team, m.team2_id)
    court = db.session.get(Court, m.court_id) if m.court_id else None
    return {
        "id": m.id,
        "round_no": m.round_no,
        "phase": m.phase,
        "is_final": is_final_phase(m),
        "status": m.status,
        "court": court.name if court else "Wartet",
        "court_id": court.id if court else None,
        "team_a": {"id": team1.id, "name": team1.name, "players": team_players(team1)},
        "team_b": {"id": team2.id, "name": team2.name, "players": team_players(team2)},
        "points": display_points(m),
        "raw_points": [m.points1, m.points2],
        "games": [m.games1, m.games2],
        "sets": [m.sets1, m.sets2],
        "score": [m.score1 or 0, m.score2 or 0],
        "winner_name": match_winner_name(m),
        "scoring_mode": m.scoring_mode,
        "server_name": serving_display_name(m),
        "server_team_name": serving_team_name(m),
        "server_player_name": serving_player_name(m),
        "server_side": serving_team_index(m),
        "in_tiebreak": in_tiebreak(m),
    }


def tournament_json(t: Tournament):
    auto_finish_round_if_needed(t)
    active = get_round_matches(t.id, t.active_round)
    courts = get_courts(t.id)
    active_by_court = {m.court_id: m for m in active if m.court_id}
    all_matches = get_matches(t.id)
    court_states = []
    for c in courts:
        cm = active_by_court.get(c.id)
        crem = court_remaining_seconds(c, t)
        cfinal = bool(cm and is_final_phase(cm))
        cwarning = bool(c.timer_running and crem <= t.warning_seconds and not c.warning_announced and not cfinal)
        court_states.append({
            "id": c.id, "name": c.name, "number": c.number, "controller_token": c.controller_token,
            "remaining_seconds": crem, "timer_running": bool(c.timer_running),
            "paused": (not c.timer_running and c.paused_remaining_seconds is not None),
            "warning_due": cwarning, "is_final": cfinal,
            "match": match_json(cm) if cm else None
        })
    rem = min([c["remaining_seconds"] for c in court_states if c.get("match") and not c.get("is_final")] or [remaining_seconds(t)])
    warning_due = any(c["warning_due"] for c in court_states)
    return {
        "public_id": t.public_id,
        "name": t.name,
        "status": t.status,
        "active_round": t.active_round,
        "round_minutes": t.round_minutes,
        "warning_seconds": t.warning_seconds,
        "remaining_seconds": rem,
        "timer_running": t.timer_running,
        "paused": (not t.timer_running and t.paused_remaining_seconds is not None),
        "scoring_mode": t.scoring_mode,
        "scoring_modes": SCORING_MODES,
        "warning_due": warning_due,
        "is_final_round": any(is_final_phase(m) for m in active),
        "courts": court_states,
        "teams": [{"id": team.id, "name": team.name, "players": team_players(team)} for team in get_teams(t.id)],
        "matches": [match_json(m) for m in all_matches],
        "table": calculate_table(t.id),
        "players": [{"id": p.id, "name": p.name} for p in get_players(t.id)],
        "bracket": bracket_data(t),
        "dark_mode": False,
    }


def emit_tournament_state(t: Tournament):
    """Sendet den aktuellen Zustand an alle offenen Geräte dieses Turniers."""
    socketio.emit("state", tournament_json(t), room=t.public_id)


@socketio.on("join_tournament")
def socket_join(data):
    public_id = (data or {}).get("public_id")
    if not public_id:
        return
    t = Tournament.query.filter_by(public_id=public_id).first()
    if not t:
        return
    join_room(t.public_id)
    emit("state", tournament_json(t))


@socketio.on("request_state")
def socket_request_state(data):
    public_id = (data or {}).get("public_id")
    if not public_id:
        return
    t = Tournament.query.filter_by(public_id=public_id).first()
    if t:
        emit_tournament_state(t)


@app.route("/")
def home():
    public_tournaments = Tournament.query.order_by(Tournament.id.desc()).limit(10).all()
    return render_template("home.html", public_tournaments=public_tournaments)


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form["username"].strip()
        password = request.form["password"]
        if not username or not password:
            return render_template("register.html", error="Bitte Benutzername und Passwort eingeben.")
        if User.query.filter_by(username=username).first():
            return render_template("register.html", error="Benutzername ist schon vergeben.")
        user = User(username=username, password_hash=generate_password_hash(password))
        db.session.add(user); db.session.commit(); login_user(user)
        return redirect(url_for("dashboard"))
    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form["username"].strip()
        password = request.form["password"]
        user = User.query.filter_by(username=username).first()
        if user and check_password_hash(user.password_hash, password):
            login_user(user)
            return redirect(url_for("dashboard"))
        return render_template("login.html", error="Login fehlgeschlagen.")
    return render_template("login.html")


@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("home"))


@app.route("/dashboard")
@login_required
def dashboard():
    tournaments = Tournament.query.filter_by(owner_id=current_user.id).order_by(Tournament.id.desc()).all()
    return render_template("dashboard.html", tournaments=tournaments)


@app.route("/create", methods=["GET", "POST"])
@login_required
def create_tournament():
    defaults = {
        "team_names": ["Team 1", "Team 2", "Team 3", "Team 4"],
        "player_names": [["Spieler 1", "Spieler 2"], ["Spieler 3", "Spieler 4"], ["Spieler 5", "Spieler 6"], ["Spieler 7", "Spieler 8"]],
        "court_names": ["Court 1", "Court 2"],
    }
    if request.method == "POST":
        name = request.form.get("name", "").strip() or "Padel Turnier"
        court_names = [x.strip() for x in request.form.get("court_names", "Court 1, Court 2").split(",") if x.strip()] or ["Court 1"]
        round_minutes = max(1, int(request.form.get("round_minutes", 20)))
        warning_seconds = max(5, int(request.form.get("warning_seconds", 120)))
        scoring_mode = request.form.get("scoring_mode", "golden_point")
        if scoring_mode not in SCORING_MODES:
            scoring_mode = "golden_point"
        double_round = request.form.get("double_round") == "on"
        team_names = request.form.getlist("team_name")
        p1_names = request.form.getlist("player1")
        p2_names = request.form.getlist("player2")
        t = Tournament(name=name, owner_id=current_user.id, courts_count=len(court_names), double_round=double_round, round_minutes=round_minutes, warning_seconds=warning_seconds, scoring_mode=scoring_mode)
        db.session.add(t); db.session.commit()
        ensure_courts(t, court_names)
        made = 0
        for i, team_name in enumerate(team_names):
            if not team_name.strip() and not (i < len(p1_names) and p1_names[i].strip()) and not (i < len(p2_names) and p2_names[i].strip()):
                continue
            p1 = Player(name=(p1_names[i].strip() if i < len(p1_names) and p1_names[i].strip() else f"Spieler {made*2+1}"), tournament_id=t.id)
            p2 = Player(name=(p2_names[i].strip() if i < len(p2_names) and p2_names[i].strip() else f"Spieler {made*2+2}"), tournament_id=t.id)
            db.session.add_all([p1, p2]); db.session.flush()
            team = Team(name=team_name.strip() or f"Team {made+1}", tournament_id=t.id, player1_id=p1.id, player2_id=p2.id)
            db.session.add(team); made += 1
        if made < 2:
            db.session.delete(t); db.session.commit()
            return render_template("create.html", error="Mindestens 2 Teams eingeben.", defaults=defaults, scoring_modes=SCORING_MODES)
        db.session.commit()
        generate_group_matches(t)
        return redirect(url_for("admin_tournament", public_id=t.public_id))
    return render_template("create.html", defaults=defaults, scoring_modes=SCORING_MODES)


@app.route("/admin/<public_id>")
@login_required
def admin_tournament(public_id):
    t = get_tournament_or_404(public_id); require_owner(t)
    return render_template("admin.html", t=t, scoring_modes=SCORING_MODES)


@app.route("/settings/<public_id>", methods=["GET", "POST"])
@login_required
def settings(public_id):
    t = get_tournament_or_404(public_id); require_owner(t)
    if request.method == "POST":
        t.name = request.form.get("name", t.name).strip() or t.name
        t.round_minutes = max(1, int(request.form.get("round_minutes", t.round_minutes)))
        t.warning_seconds = max(5, int(request.form.get("warning_seconds", t.warning_seconds)))
        mode = request.form.get("scoring_mode", t.scoring_mode)
        if mode in SCORING_MODES:
            t.scoring_mode = mode
        t.double_round = request.form.get("double_round") == "on"
        court_names = request.form.getlist("court_name")
        ensure_courts(t, [x.strip() or f"Court {i+1}" for i, x in enumerate(court_names)])
        team_ids = request.form.getlist("team_id")
        team_names = request.form.getlist("team_name")
        p1_names = request.form.getlist("player1")
        p2_names = request.form.getlist("player2")
        for i, tid in enumerate(team_ids):
            team = db.session.get(Team, int(tid))
            if team and team.tournament_id == t.id:
                team.name = team_names[i].strip() or team.name
                p1 = db.session.get(Player, team.player1_id)
                p2 = db.session.get(Player, team.player2_id)
                if p1: p1.name = p1_names[i].strip() or p1.name
                if p2: p2.name = p2_names[i].strip() or p2.name
        if request.form.get("regenerate") == "on":
            generate_group_matches(t)
        assign_courts_for_round(t)
        db.session.commit()
        return redirect(url_for("admin_tournament", public_id=t.public_id))
    return render_template("settings.html", t=t, courts=get_courts(t.id), teams=get_teams(t.id), team_players=team_players, scoring_modes=SCORING_MODES)


@app.route("/t/<public_id>")
def public_tournament(public_id):
    t = get_tournament_or_404(public_id)
    return render_template("public.html", t=t)



@app.route("/bracket/<public_id>")
def bracket(public_id):
    t = get_tournament_or_404(public_id)
    return render_template("bracket.html", t=t, data=bracket_data(t))


@app.route("/spieler/<int:player_id>")
def individual_player_stats(player_id):
    player = db.session.get(Player, player_id)
    if not player: abort(404)
    t = db.session.get(Tournament, player.tournament_id)
    row = calculate_player_stats(player.id)
    tips = ai_coach(row)
    team_ids = [team.id for team in get_teams(t.id) if player.id in [team.player1_id, team.player2_id]]
    matches = [m for m in get_matches(t.id) if m.team1_id in team_ids or m.team2_id in team_ids]
    return render_template("individual_player.html", player=player, t=t, row=row, tips=tips, matches=matches, match_winner_name=match_winner_name)

@app.route("/controller/<token>")
def controller(token):
    court = Court.query.filter_by(controller_token=token).first()
    if not court:
        abort(404)
    t = db.session.get(Tournament, court.tournament_id)

    # Robuster Zurück-Link:
    # Wenn der Controller aus Admin/Anzeige geöffnet wurde, übergeben wir return_to.
    # Falls jemand den Controller-Link direkt öffnet, fällt die App auf die öffentliche Anzeige zurück.
    back_url = request.args.get("return_to") or request.referrer or url_for("public_tournament", public_id=t.public_id)
    if not back_url.startswith("/"):
        back_url = url_for("public_tournament", public_id=t.public_id)

    return render_template("controller.html", t=t, court=court, back_url=back_url)


@app.route("/player/<int:team_id>")
def player_stats(team_id):
    team = db.session.get(Team, team_id)
    if not team: abort(404)
    t = db.session.get(Tournament, team.tournament_id)
    row = next((r for r in calculate_table(t.id) if r["id"] == team.id), None)
    tips = ai_coach(row)
    matches = [m for m in get_matches(t.id) if m.team1_id == team.id or m.team2_id == team.id]
    return render_template("player.html", team=team, t=t, row=row, tips=tips, matches=matches, match_winner_name=match_winner_name, team_players=team_players)


@app.route("/leaderboard/<public_id>")
def leaderboard(public_id):
    t = get_tournament_or_404(public_id)
    return render_template("leaderboard.html", t=t, table=calculate_table(t.id))


@app.route("/delete/<public_id>", methods=["POST"])
@login_required
def delete_tournament_route(public_id):
    t = get_tournament_or_404(public_id); require_owner(t)
    delete_tournament(t)
    db.session.commit()
    return redirect(url_for("dashboard"))


@app.route("/reset-tournament/<public_id>", methods=["POST"])
@login_required
def reset_tournament_route(public_id):
    t = get_tournament_or_404(public_id); require_owner(t)
    reset_tournament_to_round_one(t)
    db.session.commit()
    return redirect(url_for("admin_tournament", public_id=t.public_id))


@app.route("/api/t/<public_id>/state")
def api_state(public_id):
    t = get_tournament_or_404(public_id)
    return jsonify(tournament_json(t))


def jsonify_and_emit(t: Tournament):
    state = tournament_json(t)
    socketio.emit("state", state, room=t.public_id)
    return jsonify(state)


@app.route("/api/t/<public_id>/warning-seen", methods=["POST"])
def api_warning_seen(public_id):
    t = get_tournament_or_404(public_id)
    t.warning_announced = True
    db.session.commit()
    return jsonify_and_emit(t)


@app.route("/api/t/<public_id>/start", methods=["POST"])
@login_required
def api_start(public_id):
    t = get_tournament_or_404(public_id); require_owner(t)
    start_round(t)
    return jsonify_and_emit(t)


@app.route("/api/t/<public_id>/stop", methods=["POST"])
@login_required
def api_stop(public_id):
    t = get_tournament_or_404(public_id); require_owner(t)
    for c in get_courts(t.id):
        pause_court_timer(t, c)
    return jsonify_and_emit(t)


@app.route("/api/t/<public_id>/court/<int:court_id>/start", methods=["POST"])
@login_required
def api_court_start(public_id, court_id):
    t = get_tournament_or_404(public_id); require_owner(t)
    c = db.session.get(Court, court_id)
    if not c or c.tournament_id != t.id:
        abort(404)
    start_court_timer(t, c)
    return jsonify_and_emit(t)


@app.route("/api/t/<public_id>/court/<int:court_id>/stop", methods=["POST"])
@login_required
def api_court_stop(public_id, court_id):
    t = get_tournament_or_404(public_id); require_owner(t)
    c = db.session.get(Court, court_id)
    if not c or c.tournament_id != t.id:
        abort(404)
    pause_court_timer(t, c)
    return jsonify_and_emit(t)


@app.route("/api/t/<public_id>/end-round", methods=["POST"])
@login_required
def api_end_round(public_id):
    t = get_tournament_or_404(public_id); require_owner(t)
    # Runde abschließen, Ergebnisse speichern und direkt zur nächsten Runde wechseln.
    next_round(t)
    active = get_round_matches(t.id, t.active_round)
    return jsonify_and_emit(t)


@app.route("/api/t/<public_id>/next-round", methods=["POST"])
@login_required
def api_next_round(public_id):
    t = get_tournament_or_404(public_id); require_owner(t)
    next_round(t)
    return jsonify_and_emit(t)


@app.route("/api/t/<public_id>/match/<int:match_id>/point/<int:team_idx>", methods=["POST"])
def api_point(public_id, match_id, team_idx):
    t = get_tournament_or_404(public_id)
    auto_finish_round_if_needed(t)
    m = db.session.get(Match, match_id)
    if not m or m.tournament_id != t.id: abort(404)
    if team_idx not in [0, 1]: abort(400)
    # Nur laufende/aktive Runde darf gezählt werden; beendete Zeitrunden nehmen keine späten Klicks an.
    if m.round_no != t.active_round or m.status == "finished":
        return jsonify_and_emit(t)
    court = db.session.get(Court, m.court_id) if m.court_id else None
    if not is_final_phase(m):
        if not court or not court.timer_running or court_remaining_seconds(court, t) <= 0:
            return jsonify_and_emit(t)
    add_point(m, team_idx)
    db.session.commit()
    return jsonify_and_emit(t)


@app.route("/api/t/<public_id>/match/<int:match_id>/undo", methods=["POST"])
def api_undo(public_id, match_id):
    t = get_tournament_or_404(public_id)
    m = db.session.get(Match, match_id)
    if not m or m.tournament_id != t.id: abort(404)
    undo_match(m); db.session.commit()
    return jsonify_and_emit(t)


@app.route("/api/t/<public_id>/match/<int:match_id>/reset", methods=["POST"])
@login_required
def api_reset(public_id, match_id):
    t = get_tournament_or_404(public_id); require_owner(t)
    m = db.session.get(Match, match_id)
    if not m or m.tournament_id != t.id: abort(404)
    reset_score(m); db.session.commit()
    return jsonify_and_emit(t)


@app.route("/api/t/<public_id>/match/<int:match_id>/manual-score", methods=["POST"])
@login_required
def api_manual_score(public_id, match_id):
    t = get_tournament_or_404(public_id); require_owner(t)
    m = db.session.get(Match, match_id)
    if not m or m.tournament_id != t.id: abort(404)
    data = request.get_json(silent=True) or {}
    try:
        score1 = int(data.get("score1", 0))
        score2 = int(data.get("score2", 0))
    except (TypeError, ValueError):
        abort(400)
    set_manual_score(m, score1, score2)
    db.session.commit()
    return jsonify_and_emit(t)


@app.route("/api/t/<public_id>/match/<int:match_id>/mode", methods=["POST"])
def api_mode(public_id, match_id):
    t = get_tournament_or_404(public_id)
    m = db.session.get(Match, match_id)
    if not m or m.tournament_id != t.id: abort(404)
    data = request.get_json(silent=True) or {}
    mode = data.get("mode")
    if mode in SCORING_MODES:
        m.scoring_mode = mode
        db.session.commit()
    return jsonify_and_emit(t)


@app.route("/api/t/<public_id>/match/<int:match_id>/finish", methods=["POST"])
def api_finish_match(public_id, match_id):
    t = get_tournament_or_404(public_id)
    m = db.session.get(Match, match_id)
    if not m or m.tournament_id != t.id: abort(404)
    # Gruppenmatches sollen nur über den Rundenabschluss in die S/U/N-Wertung laufen.
    # Finalmatches dürfen manuell vom Controller beendet werden.
    if not is_final_phase(m):
        require_owner(t)
    finish_match(m, reason="manual"); db.session.commit()
    return jsonify_and_emit(t)


@app.route("/manifest.json")
def manifest():
    return app.send_static_file("manifest.json")


def ensure_schema_updates():
    # Kleine Hilfe für bestehende Datenbanken aus älteren ZIP-Versionen.
    # create_all() legt neue Tabellen an, ergänzt aber keine neuen Spalten.
    try:
        with db.engine.connect() as con:
            dialect = db.engine.dialect.name
            if dialect == "sqlite":
                tcols = [row[1] for row in con.exec_driver_sql("PRAGMA table_info(tournament)").fetchall()]
                if "dark_mode" not in tcols:
                    con.exec_driver_sql("ALTER TABLE tournament ADD COLUMN dark_mode BOOLEAN NOT NULL DEFAULT 0")
                if "paused_remaining_seconds" not in tcols:
                    con.exec_driver_sql("ALTER TABLE tournament ADD COLUMN paused_remaining_seconds INTEGER")
                ucols = [row[1] for row in con.exec_driver_sql("PRAGMA table_info(user)").fetchall()]
                if "dark_mode" not in ucols:
                    con.exec_driver_sql("ALTER TABLE user ADD COLUMN dark_mode BOOLEAN NOT NULL DEFAULT 0")
                ccols = [row[1] for row in con.exec_driver_sql("PRAGMA table_info(court)").fetchall()]
                for col, ddl in {
                    "timer_running": "BOOLEAN NOT NULL DEFAULT 0",
                    "round_started_at": "DATETIME",
                    "round_ends_at": "DATETIME",
                    "paused_remaining_seconds": "INTEGER",
                    "warning_announced": "BOOLEAN NOT NULL DEFAULT 0",
                }.items():
                    if col not in ccols:
                        con.exec_driver_sql(f"ALTER TABLE court ADD COLUMN {col} {ddl}")
                mcols = [row[1] for row in con.exec_driver_sql("PRAGMA table_info(match)").fetchall()]
                for col, ddl in {
                    "serving_team": "INTEGER NOT NULL DEFAULT 0",
                    "team1_server_player": "INTEGER NOT NULL DEFAULT 0",
                    "team2_server_player": "INTEGER NOT NULL DEFAULT 0",
                    "team1_has_served": "BOOLEAN NOT NULL DEFAULT 1",
                    "team2_has_served": "BOOLEAN NOT NULL DEFAULT 0",
                }.items():
                    if col not in mcols:
                        con.exec_driver_sql(f"ALTER TABLE match ADD COLUMN {col} {ddl}")
                con.commit()
            elif dialect in {"postgresql", "postgres"}:
                con.exec_driver_sql("ALTER TABLE tournament ADD COLUMN IF NOT EXISTS dark_mode BOOLEAN NOT NULL DEFAULT FALSE")
                con.exec_driver_sql("ALTER TABLE tournament ADD COLUMN IF NOT EXISTS paused_remaining_seconds INTEGER")
                con.exec_driver_sql('ALTER TABLE "user" ADD COLUMN IF NOT EXISTS dark_mode BOOLEAN NOT NULL DEFAULT FALSE')
                con.exec_driver_sql('ALTER TABLE court ADD COLUMN IF NOT EXISTS timer_running BOOLEAN NOT NULL DEFAULT FALSE')
                con.exec_driver_sql('ALTER TABLE court ADD COLUMN IF NOT EXISTS round_started_at TIMESTAMP')
                con.exec_driver_sql('ALTER TABLE court ADD COLUMN IF NOT EXISTS round_ends_at TIMESTAMP')
                con.exec_driver_sql('ALTER TABLE court ADD COLUMN IF NOT EXISTS paused_remaining_seconds INTEGER')
                con.exec_driver_sql('ALTER TABLE court ADD COLUMN IF NOT EXISTS warning_announced BOOLEAN NOT NULL DEFAULT FALSE')
                con.exec_driver_sql('ALTER TABLE "match" ADD COLUMN IF NOT EXISTS serving_team INTEGER NOT NULL DEFAULT 0')
                con.exec_driver_sql('ALTER TABLE "match" ADD COLUMN IF NOT EXISTS team1_server_player INTEGER NOT NULL DEFAULT 0')
                con.exec_driver_sql('ALTER TABLE "match" ADD COLUMN IF NOT EXISTS team2_server_player INTEGER NOT NULL DEFAULT 0')
                con.exec_driver_sql('ALTER TABLE "match" ADD COLUMN IF NOT EXISTS team1_has_served BOOLEAN NOT NULL DEFAULT TRUE')
                con.exec_driver_sql('ALTER TABLE "match" ADD COLUMN IF NOT EXISTS team2_has_served BOOLEAN NOT NULL DEFAULT FALSE')
                con.commit()
    except Exception:
        pass


with app.app_context():
    db.create_all()
    ensure_schema_updates()


if __name__ == "__main__":
    socketio.run(app, host="0.0.0.0", port=5000, debug=False, allow_unsafe_werkzeug=True)
