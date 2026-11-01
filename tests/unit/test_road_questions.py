from src.presentation import road_questions


class Rows:
    def __init__(self, rows):
        self.rows, self.sql = rows, None

    def query(self, sql):
        self.sql = sql
        return self.rows


def test_road_questions_are_recognised():
    assert road_questions.choose("What causes most road disruptions?") == "types"
    assert road_questions.choose("Show a pie chart of road incident types") == "types"
    assert road_questions.choose("Which roads have the most accidents?") == "areas"
    assert road_questions.choose("How many road accidents are there each day?") == "daily"
    assert road_questions.choose("Are there any road incidents right now?") is None   # live feed
    assert road_questions.choose("What causes most rail disruption?") is None
    assert road_questions.choose("When do most road accidents happen?") is None      # the hourly analysis


def test_types_answer_and_pie():
    rows = Rows([{"x": "Vehicle collision", "y": 29, "clear": 130}, {"x": "Vehicle breakdown", "y": 18, "clear": 68},
                 {"x": "Traffic signal fault", "y": 5, "clear": 359}])
    out = road_questions.run("types", "Show a pie chart of road incident types", rows)
    assert "is_incident" in rows.sql and out["chart"]["kind"] == "pie"
    assert out["answer"].startswith("**Vehicle collision** is the most common of the 52 road incidents")
    assert "Traffic signal fault (typically 5h 59m" in out["answer"]


def test_accidents_by_day_and_area():
    rows = Rows([{"x": "Thu 29 Oct", "y": 3}, {"x": "Fri 30 Oct", "y": 7}, {"x": "Sat 31 Oct", "y": 19}])
    out = road_questions.run("daily", "How many road accidents are there each day?", rows)
    assert "category = 'Collisions'" in rows.sql and out["answer"].startswith("**About 9.7 collisions a day**")
    areas = road_questions.run("areas", "Which areas have the most accidents?",
                               Rows([{"x": "Greenwich", "y": 4}, {"x": "Westminster", "y": 3}]))
    assert "**Greenwich** had the most collisions" in areas["answer"]
