"""The "Ask the data" assistant: routing a question and answering from the sources."""

from src.presentation import assistant, bus_recommendation
from src.presentation.assistant import route
from tests.unit.test_bus_recommendation import ROW as BUS_ROW


class Sources:
    def line_cards(self):
        return [
            {"line_name": "Central", "status": "Minor Delays", "other_statuses": [], "level": "delays",
             "duration": "25 min", "reasons": ["Central Line: Minor delays due to a train fault."]},
            {"line_name": "Victoria", "status": "Good Service", "other_statuses": [], "level": "good",
             "duration": None, "reasons": []},
        ]

    def bike_rows(self):
        return [
            {"name": "Waterloo Station 1, Waterloo", "bikes": 3, "e_bikes": 1, "empty_docks": 1, "state": "ok"},
            {"name": "Waterloo Road, South Bank", "bikes": 0, "e_bikes": 0, "empty_docks": 14, "state": "empty"},
            {"name": "Hyde Park Corner, Hyde Park", "bikes": 10, "e_bikes": 2, "empty_docks": 0, "state": "full"},
        ]

    def roads(self):
        disruptions = [
            {"is_incident": True, "category": "Collisions", "sub_category": "Vehicle collision", "severity": "Serious",
             "weight": 3, "location": "[A12] Eastway (E9)", "roads": ["A12"]},
            {"is_incident": False, "category": "Works", "sub_category": "", "severity": "Moderate", "weight": 2,
             "location": "[A3] Kingston Road", "roads": ["A3"]},
        ]
        corridors = [{"id": "a12", "name": "A12", "description": "Serious", "good": False},
                     {"id": "a2", "name": "A2", "description": "No Exceptional Delays", "good": True}]
        return disruptions, corridors

    def recommendation(self, area):
        return (BUS_ROW, bus_recommendation) if area == "bus" else None

    def docs(self, query):
        return [{"title": "Buses: bunching", "text": "A gap is bunched when it is under half...",
                 "source": "docs/agent/glossary.md"}] if "bunch" in query.lower() else []


def ask(question):
    return assistant.answer(question, Sources())["answer"]


def test_routing():
    assert route("Which Tube lines have problems right now?") == ("rail", "now")
    assert route("Which lines were least reliable this week?") == ("rail", "history")
    assert route("How many bikes are at Waterloo?") == ("bikes", "now")
    assert route("Which main roads are delayed?") == ("roads", "now")
    assert route("Which bus routes bunch most?") == ("bus", "history")
    assert route("What does bunching mean?")[0] == "definition"
    assert route("Why use the median time to clear?")[0] == "definition"
    assert route("Why is the Central line delayed?") == ("rail", "now")
    assert route("hello")[0] is None


def test_rail_now():
    assert ask("Which lines have problems right now?").startswith("1 of 2 lines have problems right now; 1 have")
    assert ask("Is the Central line running?") == (
        "**Central: Minor Delays**, for 25 min. Central Line: Minor delays due to a train fault.")


def test_bikes_now():
    answer = ask("How many bikes are at Waterloo?")
    assert answer.startswith('2 docking stations match "waterloo" right now')
    assert "**Waterloo Road, South Bank**: 0 bikes (0 e-bikes), 14 free docks — empty" in answer
    assert "**Waterloo Station 1, Waterloo**: 3 bikes (1 e-bike), 1 free dock" in answer
    assert "13 bikes" in ask("How many bikes are there?")
    assert "couldn't find" in ask("bikes near Narnia")


def test_roads_now():
    assert ask("Which main roads are delayed?").startswith("**1 of 2 main roads** are rated Serious or worse right now: A12.")
    assert ask("How is the A12?").startswith("**A12**: TfL rates it Serious. 1 live incident and 0 roadworks are on it.")


def test_history_and_definitions():
    assert ask("Which bus routes bunch most?").startswith("Uneven bus spacing adds about 0.8 minutes")
    assert ask("What does bunching mean?").startswith("**Buses: bunching**")
    assert "aren't available" in ask("Which lines were least reliable this week?")


def test_a_failing_source_does_not_break_the_chat():
    class Broken(Sources):
        def line_cards(self):
            raise TimeoutError
    assert "isn't responding" in assistant.answer("Is the Central line running?", Broken())["answer"]


def test_greetings_get_a_reply_and_suggestions():
    hello = assistant.answer("Hello!", Sources())
    assert hello["answer"].startswith("Hello! I can answer questions about London's transport")
    assert hello["suggestions"] == assistant.SUGGESTIONS
    assert assistant.answer("good morning", Sources())["answer"].startswith("Hello!")
    assert assistant.answer("thanks", Sources())["answer"].startswith("You're welcome!")
    assert assistant.answer("bye", Sources())["suggestions"] == []
    # A greeting with a question is answered as the question
    assert ask("Hi, is the Central line running?").startswith("**Central: Minor Delays**")


def test_off_topic_questions_are_declined_with_suggestions():
    for question in ("What's the weather like?", "Who won the football?", "Write me a poem", "What is love?"):
        result = assistant.answer(question, Sources())
        assert result["answer"].startswith("Sorry, I can only answer questions about this TfL project"), question
        assert result["suggestions"] == assistant.SUGGESTIONS


def test_questions_about_the_platform_are_in_scope():
    assert route("How does the platform collect data?")[0] == "definition"
    assert route("What is a headway?")[0] == "definition"
    answered = assistant.answer("What does bunching mean?", Sources())
    assert answered["source"] == "documentation search" and answered["suggestions"] == []
