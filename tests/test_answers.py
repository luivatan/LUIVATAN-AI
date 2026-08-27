from apex_answers import Citation, AnswerEngine, decompose_question, evaluate_answer, rewrite_query, source_viewer
from apex_retrieval import Result


def test_query_tools():
    assert decompose_question("What is dose and what are risks?") == ["What is dose?", "what are risks?"]
    assert "previous question" in rewrite_query("What about it?", [{"user": "What is dosage?"}])


def test_grounded_answer_and_evaluation():
    result = Result("Use with food.", {"source": "guide.pdf", "page": 4}, 1)
    answer, citations = AnswerEngine(lambda prompt: "Use with food [1].").answer("How?", [result])
    assert citations[0].label() == "[1] guide.pdf, page 4"
    assert evaluate_answer(answer, citations)["has_evidence"] == 1
    assert "guide.pdf" in source_viewer(citations[0])
