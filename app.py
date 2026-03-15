import json
from pathlib import Path

from flask import Flask, render_template, request

app = Flask(__name__)
BASE_DIR = Path(__file__).resolve().parent
CONFIDENCE_THRESHOLD = 0.60


def load_json(filename):
    with open(BASE_DIR / filename, "r", encoding="utf-8") as file:
        return json.load(file)


def save_json(filename, data):
    with open(BASE_DIR / filename, "w", encoding="utf-8") as file:
        json.dump(data, file, indent=2)


docs = load_json("docs.json")
ambiguous = load_json("ambiguous_terms.json")
semantic_layer = load_json("semantic_layer.json")
semantic_memory = load_json("semantic_memory.json")


def normalize_text(text):
    return " ".join(text.strip().lower().split())


def apply_aliases(query):
    normalized_query = normalize_text(query)
    for alias, canonical in semantic_layer["language_aliases"].items():
        normalized_query = normalized_query.replace(alias, canonical)
    return normalized_query


def render_home(view_model):
    return render_template(
        "index.html",
        departments=semantic_layer["departments"],
        **view_model,
    )


def find_ambiguous_term(query):
    normalized_query = apply_aliases(query)
    for term, meanings in ambiguous.items():
        if term in normalized_query:
            return term, meanings, normalized_query
    return None, None, normalized_query


def find_documented_answer(query):
    normalized_query = apply_aliases(query)
    for key, answer in docs["general"].items():
        if key in normalized_query:
            return answer
    return None


def score_meanings(term, meanings, department, normalized_query):
    term_config = semantic_layer["term_profiles"].get(term, {})
    department_weights = term_config.get("department_weights", {})
    keyword_weights = term_config.get("keyword_weights", {})
    learned_weights = semantic_memory.get("confirmations", {}).get(term, {})
    department_learning = (
        semantic_memory.get("department_learning", {})
        .get(department, {})
        .get(term, {})
    )

    scores = {}
    for meaning in meanings:
        score = 1.0
        score += department_weights.get(department, {}).get(meaning, 0)
        score += learned_weights.get(meaning, 0) * 0.18
        score += department_learning.get(meaning, 0) * 0.25

        for hint in keyword_weights.get(meaning, []):
            if hint in normalized_query:
                score += 0.35

        scores[meaning] = round(score, 3)

    total_score = sum(scores.values()) or 1.0
    ranked = sorted(
        (
            (meaning, round(score / total_score, 2))
            for meaning, score in scores.items()
        ),
        key=lambda item: item[1],
        reverse=True,
    )
    return ranked


def build_confidence_summary(ranked_meanings):
    return [
        {"meaning": meaning, "confidence": confidence}
        for meaning, confidence in ranked_meanings
    ]


def get_contextual_response(term, meaning, normalized_query):
    response = docs["contextual"].get(term, {}).get(meaning)
    if response:
        return response

    return (
        "Knowledge Gap Detected: No enterprise documentation is available for "
        f'"{term}" interpreted as "{meaning}" in the query "{normalized_query}".'
    )


def learn_from_confirmation(term, meaning, department):
    confirmations = semantic_memory.setdefault("confirmations", {})
    term_memory = confirmations.setdefault(term, {})
    term_memory[meaning] = term_memory.get(meaning, 0) + 1

    department_memory = semantic_memory.setdefault("department_learning", {})
    department_term_memory = department_memory.setdefault(department, {}).setdefault(term, {})
    department_term_memory[meaning] = department_term_memory.get(meaning, 0) + 1

    save_json("semantic_memory.json", semantic_memory)


def resolve_selected_meaning(original_query, pending_term, selected_meaning, department):
    normalized_query = apply_aliases(original_query)
    valid_meanings = ambiguous.get(pending_term, [])

    if selected_meaning not in valid_meanings:
        return None, f'Please choose a valid enterprise meaning for "{pending_term}".'

    learn_from_confirmation(pending_term, selected_meaning, department)
    response = get_contextual_response(pending_term, selected_meaning, normalized_query)
    return selected_meaning, response


def build_detection_response(term, department, normalized_query):
    meanings = ambiguous.get(term, [])
    ranked_meanings = score_meanings(term, meanings, department, normalized_query)
    confidence_summary = build_confidence_summary(ranked_meanings)
    top_meaning, top_confidence = ranked_meanings[0]

    if top_confidence >= CONFIDENCE_THRESHOLD:
        response = (
            f'Based on {department} context, "{term}" most likely refers to '
            f'"{top_meaning}" ({int(top_confidence * 100)}% confidence). '
            "Confirm interpretation or choose another meaning."
        )
    else:
        response = (
            f'I detected semantic ambiguity for "{term}". '
            "Please choose the intended enterprise meaning."
        )

    return response, confidence_summary, top_meaning


@app.route("/", methods=["GET", "POST"])
def home():
    view_model = {
        "query": "",
        "response": "",
        "department": "General",
        "original_query": "",
        "pending_term": "",
        "clarification_options": [],
        "confidence_summary": [],
        "predicted_meaning": "",
        "resolved_meaning": "",
    }

    if request.method == "POST":
        query = request.form.get("query", "").strip()
        department = request.form.get("department", "General").strip() or "General"
        original_query = request.form.get("original_query", "").strip()
        pending_term = request.form.get("pending_term", "").strip().lower()
        selected_meaning = request.form.get("selected_meaning", "").strip().lower()

        view_model["query"] = query
        view_model["department"] = department

        if pending_term and original_query and selected_meaning:
            resolved_meaning, response = resolve_selected_meaning(
                original_query,
                pending_term,
                selected_meaning,
                department,
            )
            view_model.update(
                {
                    "response": response,
                    "original_query": original_query,
                    "resolved_meaning": resolved_meaning or "",
                }
            )
            return render_home(view_model)

        term, meanings, normalized_query = find_ambiguous_term(query)
        if term:
            response, confidence_summary, predicted_meaning = build_detection_response(
                term, department, normalized_query
            )
            view_model.update(
                {
                    "query": "",
                    "response": response,
                    "original_query": query,
                    "pending_term": term,
                    "clarification_options": meanings,
                    "confidence_summary": confidence_summary,
                    "predicted_meaning": predicted_meaning,
                }
            )
            return render_home(view_model)

        answer = find_documented_answer(query)
        if answer:
            view_model["response"] = answer
            return render_home(view_model)

        view_model["response"] = "Knowledge Gap Detected: No documentation found."

    return render_home(view_model)


if __name__ == "__main__":
    app.run(debug=True)
