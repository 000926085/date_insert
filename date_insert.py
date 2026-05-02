## Imports
import requests
import json
import firebase_admin
import calendar

from datetime import datetime, timedelta
from firebase_admin import credentials, firestore
from datetime import datetime, timezone

## Constants
URL_DOMAIN = "https://api-web.nhle.com"
SHOT_TYPES = {"shot-on-goal", "missed-shot", "blocked-shot", "goal"}

## Functions
def connect_to_firebase():
    """
    Connect to Firestore Database.

    returns:
        firestore db connection
    """
    cred = credentials.Certificate("serviceAccountKey.json")
    firebase_admin.initialize_app(cred)

    db = firestore.client()
    return db

def format_date(date_str):
    """
    Helper method to format a date, just in case.

    args:
        date_str: string representation of a date.
    returns:
        correctly formatted date string, None if invalid or beyond today's date.
    """
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        if dt.date() > datetime.now().date():
            return None

        return dt.strftime("%Y-%m-%d")
    except ValueError:
        return None

def get_date_range(start_str, end_str):
    try:
        start_date = datetime.strptime(start_str, "%Y-%m-%d").date()
        end_date = datetime.strptime(end_str, "%Y-%m-%d").date()

        today = datetime.now().date()
        if end_date > today:
            end_date = today

        delta = end_date - start_date
        return [(start_date + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(delta.days + 1)]
    except ValueError:
        return []

def get_ids_from_date(date):
    """
    Calls the NHL API to find the ids of a game.

    args:
        date: string, valid date to find games for.
    returns:
        array of ids for a given date.
    """
    response = requests.get(f"{URL_DOMAIN}/v1/schedule/{date}")

    try:
        json = response.json()
        games = json.get("gameWeek", [])

        # Find the dict for the date and return the ids of games for said date.
        data = next((i for i in games if i.get("date") == date), None)
        return [game.get("id") for game in data.get("games", [])]

    except Exception as e:
        print(f"Exception! {e}")
        return None

def get_game_data(id):
    """
    Calls the NHL API to fetch the play-by-play data for a given game.

    args:
        id: int, id for a game.
    returns:
        json response containing game data.
    """
    response = requests.get(f"{URL_DOMAIN}/v1/gamecenter/{id}/play-by-play")

    try:
        json = response.json()
        return json

    except Exception as e:
        print(f"Exception! {e}")
        return None

def get_strength_state(sit_code):
    """
    Helper method to determine the strength state of a shot based on the situation code.

    args:
        sit_code: int representing the skaters on each side.
    return:
        string representation of situation code, None if the code could not be identified.
    """
    even_tup = ('1551', '1441', '1331', '0660', '0550', '0440')
    if sit_code in even_tup:
        return 'EVEN'

    away_goalie, away_players, home_players, home_goalie = sit_code[0], sit_code[1], sit_code[2], sit_code[3]

    if home_goalie == "0": return "HOME EMPTY NET"
    elif away_goalie == "0": return "AWAY EMPTY NET"
    elif home_players > away_players:  return 'HOME PP'
    elif away_players > home_players:  return 'AWAY PP'

    return None

def get_team_abbr_from_id(teams, team_id):
    """
    Helper method to find the abbreviation of a team based on the team id.

    args:
        teams: array, containing data for each team.
        team_id: int, id of the team we are finding the abbrev for.
    returns:
        string representing team abbreviation, or None as a failsafe.
    """
    if team_id != None:
        if teams[0].get('id') == team_id:
            return teams[0].get('abbrev')
        elif teams[1].get('id') == team_id:
            return teams[1].get('abbrev')

    return None

def clean_roster_data(roster, team_ids):
    """
    Extracts the fields for a player to place within teams -> away/home.

    args:
        roster: dict, contains data for each participating player.
        team_ids: array, contains the two team ids.
    returns:
        2d array, players on home team are placed at index 0, and away at 1.
    """
    players = [[], []]
    for player in roster:
        player_details = {
                    'player': player.get('firstName', {}).get('default') + " " + player.get('lastName', {}).get('default'),
                    'sweaterNumber': player.get('sweaterNumber')
                }
        if player.get('teamId') == team_ids[0]:
            players[0].append(player_details)
        elif player.get('teamId') == team_ids[1]:
            players[1].append(player_details)

    return players

def get_shot_data(data):
    """
    Iterates over plays and returns only shots.

    args:
        data: dict, every play attributed to a game.
    returns:
        dict, every shot attributed to a game.
    """
    shots = []

    for p in data:
        if p.get("typeDescKey") in SHOT_TYPES:
            shots.append(p)

    return shots

def get_play_data(data, categories):
    """
    Generalized method to find plays within the categories.

    args:
        data: dict, every play attributed to a game.
        categories: list, strings of the type of plays to search for.
    returns:
        dict, every matched play attributed to a game
    """
    plays = []

    for p in data:
        if p.get("typeDescKey") in categories:
            plays.append(p)

    return plays

def clean_shot_data(shots, teams, roster):
    """
    Extracts the required fields from a shot to prepare for storage.

    args:
        shots: dict, shots returned from get_shot_data.
        teams: array, contains 2 dicts representing teams.
        roster: dict, contains participating players for a game.
    returns:
        array of shots containing the appropriate fields.
    """
    cleaned_shots = []
    shot_id = 1

    player_lookup = {
        p.get("playerId"): {
            "name": f"{p.get('firstName', {}).get('default', '')} {p.get('lastName', {}).get('default', '')}",
            "sweaterNumber": p.get("sweaterNumber"),
            "teamId": p.get("teamId"),
            "position": p.get("positionCode")
        }
        for p in roster
    }

    for s in shots:
        details = s.get("details", {})
        team_id = str(details.get("eventOwnerTeamId"))
        is_goal = s.get('typeDescKey') == 'goal'

        raw_player_id = details.get("scoringPlayerId") if is_goal else details.get("shootingPlayerId")
        player_info = player_lookup.get(raw_player_id)

        shot_info = {
            "id": shot_id,
            "coords": {
                "xCoord": details.get("xCoord"),
                "yCoord": details.get("yCoord")
            },
            "player": {
                "shootingPlayer": player_info["name"],
                "sweaterNumber": player_info["sweaterNumber"],
                "teamId": player_info["teamId"],
                "position": player_info["position"]
            },
            "eventOwnerTeam": get_team_abbr_from_id(teams, details.get("eventOwnerTeamId")),
            "strengthState": get_strength_state(s.get("situationCode")),
            "typeDescKey": s.get("typeDescKey"),
            "period": {
                "number": s.get("periodDescriptor", {}).get("number"),
                "periodType": s.get("periodDescriptor", {}).get("periodType"),
                "timeRemaining": s.get("timeRemaining"),
                "timeInPeriod": s.get("timeInPeriod"),
            }
        }

        if is_goal:
            shot_info["assists"] = {
                "assist1": player_lookup.get(details.get("assist1PlayerId"), {}).get("name"),
                "assist2": player_lookup.get(details.get("assist2PlayerId"), {}).get("name"),
            }

        shot_id += 1
        cleaned_shots.append(shot_info)

    return cleaned_shots

def total_penalty_minutes(penalties, home_id, away_id):
    """
    Totals the penalty minutes accrued over the course of a game for both teams.

    args:
        penalties: list of plays that are deemed penalties.
        home_id: numerical id of the home team.
        away_id: numerical id of the away team.
    returns:
        two numerical values that represent the penalty minutes for each team.
    """
    home_penalties = 0
    away_penalties = 0

    for p in penalties:
        duration = p.get("details", {}).get("duration", 0)
        team_id = p.get("details", {}).get("eventOwnerTeamId")

        if team_id == away_id:
            away_penalties += duration
        elif team_id == home_id:
            home_penalties += duration

    return home_penalties, away_penalties

def get_powerplay_stats(plays, home_id, away_id):
    """
    Finds the powerplay statistics for each team.

    args:
        plays: dict of all plays made during a game.
        home_id: int value of a home's id
        away_id: int value of a away's id
    returns:
        dict containing opportunities and powerplay goals of each team.
    """
    pp_stats = {
        "home": {"opportunities": 0, "goals": 0},
        "away": {"opportunities": 0, "goals": 0},
    }

    prev_situation = None
    for i in range(len(plays)):
        play = plays[i]
        current_situation = play.get("situationCode", "1551")

        if prev_situation:
            prev_home = int(prev_situation[2]) + int(prev_situation[3])
            prev_away = int(prev_situation[1]) + int(prev_situation[0])

            curr_home = int(current_situation[2]) + int(current_situation[3])
            curr_away = int(current_situation[1]) + int(current_situation[0])

            if prev_home == prev_away:
                if curr_home > curr_away:
                    pp_stats["home"]["opportunities"] += 1
                elif curr_away > curr_home:
                    pp_stats["away"]["opportunities"] += 1

        if plays[i]["typeDescKey"] == "goal":
            goal_situation = plays[i].get("situationCode", "1551")
            home_players = int(goal_situation[2]) + int(goal_situation[3])
            away_players = int(goal_situation[1]) + int(goal_situation[0])

            scoring_team = plays[i].get("details", {}).get("eventOwnerTeamId")

            if scoring_team == teams[0].get('id') and away_players > home_players:
                pp_stats["away"]["goals"] += 1
            elif scoring_team == teams[1].get('id') and home_players > away_players:
                pp_stats["home"]["goals"] += 1

        prev_situation = current_situation

    return pp_stats

## Main
db = connect_to_firebase()
adding = True

while adding:
    mode = input("(1) Single date or (2) date range? >>> ")
    date_list = []

    if mode == "2":
        start_input = input("Start date (yyyy-mm-dd) >>> ")
        end_input = input("End date (yyyy-mm-dd) >>> ")
        date_list = get_date_range(start_input, end_input)
    else:
        user_input = input("Provide a date (yyyy-mm-dd) >>> ")
        formatted = format_date(user_input)
        if formatted:
            date_list = [formatted]

    if date_list:
        for current_date in date_list:
            ids = get_ids_from_date(current_date)

            if not ids:
                print(f"No games found for {current_date}.")
                continue

            str_ids = [str(game_id) for game_id in ids]

            if len(str_ids) != 0:
                doc_ref = db.collection('Games').document(current_date)
                doc_snapshot = doc_ref.get()

                if doc_snapshot.exists:
                    print(f"{current_date} is already recorded in the database.")
                else:
                    successful_game_ids = []

                    for i in str_ids:
                        try:
                            print(f"Storing gameId: {i}")
                            game_batch = db.batch()

                            data = get_game_data(i)
                            plays = data.get("plays", [])
                            shots = get_play_data(plays, SHOT_TYPES)
                            teams = [data.get("homeTeam"), data.get("awayTeam")]
                            clock = data.get("clock", {})
                            periodDescriptor = data.get("periodDescriptor", {})

                            penalties = get_play_data(plays, {"penalty"})
                            penalty_minutes = total_penalty_minutes(penalties, teams[0].get('id'), teams[1].get('id'))
                            powerplay_stats = get_powerplay_stats(plays, teams[0].get('id'), teams[1].get('id'))

                            roster = data.get("rosterSpots")
                            cleaned_roster = clean_roster_data(roster, [teams[0].get('id'), teams[1].get('id')])

                            game_ref = doc_ref.collection(str(i)).document("gameData")
                            game_batch.set(game_ref, {
                                "gameDate": current_date,
                                "gameState": data.get("gameState"),
                                "homeTeamDefendingSide": shots[0].get("homeTeamDefendingSide", "left"),
                                "lastUpdated": datetime.now(timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z'),
                                "period": {
                                    "inIntermission": clock.get("inIntermission"),
                                    "number": periodDescriptor.get("number"),
                                    "periodType": periodDescriptor.get("periodType"),
                                    "timeRemaining": clock.get("timeRemaining")
                                },
                                "shots": clean_shot_data(shots, teams, roster),
                                "startTimeUTC": data.get("startTimeUTC"),
                                "strengthState": "EVEN",
                                "teams": {
                                    "away": {
                                        "abbrev": teams[1].get("abbrev"),
                                        "name": teams[1].get("commonName", {}).get("default"),
                                        "players": cleaned_roster[1],
                                        "score": teams[1].get("score"),
                                        "powerplays": powerplay_stats["away"],
                                        "penaltyMinutes": penalty_minutes[1],
                                        "team_id": int(teams[1].get("id"))
                                    },
                                    "home": {
                                        "abbrev": teams[0].get("abbrev"),
                                        "name": teams[0].get("commonName", {}).get("default"),
                                        "players": cleaned_roster[0],
                                        "score": teams[0].get("score"),
                                        "powerplays": powerplay_stats["home"],
                                        "penaltyMinutes": penalty_minutes[0],
                                        "team_id": int(teams[1].get("id"))
                                    }
                                }
                            })

                            game_batch.commit()
                            successful_game_ids.append(i)

                        except Exception as e:
                            print(f"Failed to store game {i}: {e}")
                            continue

                    if successful_game_ids:
                        doc_ref.set({
                            "games": successful_game_ids
                        })
                        print(f"Data for {current_date} was successfully stored.")

    else:
        print("Invalid date(s) provided.")

    print("\n")