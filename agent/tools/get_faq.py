"""
Tool: get_faq
Returns FAQ content for the USD calling agent by delegating to the standalone KnowledgeRetriever.
Supported languages: en, hi, gu.
"""

from typing import Optional, Dict, Any
from agent.knowledge import get_retriever, get_guidance_retriever

_NOT_FOUND = {
    "en": (
        "I don't have specific information on that right now. "
        "For more details, I'd recommend visiting our official website at ultimatesmiledesign.com."
    ),
    "hi": (
        "मेरे पास अभी इसकी पूरी जानकारी नहीं है। "
        "अधिक जानकारी के लिए, आप हमारी वेबसाइट ultimatesmiledesign.com पर जा सकते हैं।"
    ),
    "gu": (
        "મારી પાસે અત્યારે આની પૂરી માહિતી નથી. "
        "વધારે માહિતી માટે, તમે અમારી વેબસાઇટ ultimatesmiledesign.com ની મુલાકાત લઈ શકો છો."
    ),
}

SUPPORTED_TOPICS = [
    "about_usd", "about_ade_haresh_savani", "safety_quality_materials",
    "warranty", "veneers", "implants", "whitening", "treatments_general",
    "preview_ai_digital", "process_timeline", "cities_coverage", "cost_value",
    "aftercare_comfort", "privacy_busy_schedule", "contact_booking",
    "process", "timeline", "cities", "cost", "before_after",
    "join_usd", "for_dentists", "join_team", "partner_dentist",
    "course_price", "dentist_course", "dentist_partner_benefits",
    "local_dentist_vs_usd", "profession_guidance"
]


TOPIC_ALIASES = {
    "before_after": "ai_smile_preview",
    "before-after": "ai_smile_preview",
    "before after": "ai_smile_preview",
    "digital_preview": "ai_smile_preview",
    "smile_preview": "ai_smile_preview",
    "preview": "ai_smile_preview",
    "prices": "cost_value",
    "cost": "cost_value",
    "price": "cost_value",
    "timeline": "process_timeline",
    "process": "process_timeline",
    "ade_haresh_savani": "about_ade_haresh_savani",
    "haresh_savani": "about_ade_haresh_savani",
    "cities": "cities_coverage",
    "city": "cities_coverage",
    "local_dentist": "local_dentist_vs_usd",
    "local_dentist_comparison": "local_dentist_vs_usd",
    "dentist_comparison": "local_dentist_vs_usd",
    "other_dentist": "local_dentist_vs_usd",
}


def get_faq(topic: str, language: str = "en") -> Dict[str, Any]:
    """
    Returns FAQ content or conversational guidance (including profession-specific smile reframes)
    for the requested topic or keyword query in the requested language.
    Delegates retrieval to the modular KnowledgeRetriever and GuidanceRetriever architectures.
    
    Args:
        topic: Topic name, profession, or keyword query string (e.g., 'whitening', 'cost', 'doctor', 'teacher', 'profession_lawyer').
        language: en | hi | gu | multi.

    Returns:
        A dict with:
          - found (bool): whether matching knowledge was retrieved.
          - topic (str): queried topic or keywords.
          - language (str): language used.
          - answer (str): the retrieved FAQ answer or conversational guidance in the requested language.
          - score (float): retrieval relevance score.
          - related_topics (list): suggested follow-up topics.
    """
    query_topic = topic.strip()
    target_topic = TOPIC_ALIASES.get(query_topic.lower(), query_topic)
    lang = language.strip().lower() if language.strip().lower() in ("en", "hi", "gu") else "en"
    clean_target = target_topic.lower()
    
    # 1. Check if the query is explicitly guidance, profession, or objection related
    is_guidance_query = (
        clean_target.startswith("profession")
        or clean_target.startswith("guidance")
        or clean_target.startswith("objection")
        or clean_target.startswith("reframe")
        or "profession" in clean_target
        or "objection" in clean_target
        or clean_target in ("profession_guidance", "objection_handling", "privacy_framing", "conversation_coaching")
    )

    # PATH A: Explicit Guidance / Behavioral Path
    if is_guidance_query:
        guidance_retriever = get_guidance_retriever()
        guidance_results = guidance_retriever.retrieve(query=query_topic, topic=target_topic, top_k=2, threshold=0.5, language=lang)
        if not guidance_results and target_topic != query_topic:
            guidance_results = guidance_retriever.retrieve(query=target_topic, topic=target_topic, top_k=2, threshold=0.5, language=lang)
        
        if guidance_results:
            primary = guidance_results[0]
            return {
                "found": True,
                "topic": primary["topic"] or query_topic,
                "answer": primary["content"],
                "score": primary["score"],
                "related_topics": primary.get("related_topics", []),
                "instruction": (
                    "Use this guidance/reframing to personalize your response to the caller. "
                    "Respond naturally in the language of the caller's CURRENT spoken turn. "
                    "Do not read out instructions or metadata; speak conversationally as Kiara."
                )
            }
        return {
            "found": False,
            "topic": query_topic,
            "answer": _NOT_FOUND.get(lang, _NOT_FOUND["en"]),
            "score": 0.0,
            "related_topics": [],
            "instruction": (
                "Use this guidance to respond naturally to the caller. "
                "Respond entirely in the language of the caller's CURRENT spoken turn."
            )
        }

    # PATH B: Factual Knowledge Path ONLY (Never substitute guidance for factual queries)
    retriever = get_retriever()
    results = retriever.retrieve(query=target_topic, topic=target_topic, top_k=2, threshold=0.25, language=lang)
    if not results and target_topic != query_topic:
        results = retriever.retrieve(query=query_topic, topic=query_topic, top_k=2, threshold=0.25, language=lang)

    if not results or results[0]["score"] < 0.25:
        return {
            "found": False,
            "topic": query_topic,
            "answer": _NOT_FOUND.get(lang, _NOT_FOUND["en"]),
            "score": 0.0,
            "related_topics": [],
            "instruction": (
                "Use these facts to answer the caller. "
                "Respond entirely in the language of the caller's CURRENT spoken turn. "
                "Do not let the language of this tool result determine the response language."
            )
        }
    
    # Combine answers if multiple highly relevant items returned
    primary = results[0]
    answer = primary["content"]
    related = list(primary["related_topics"])
    
    if len(results) > 1 and results[1]["score"] > 0.35:
        second_content = results[1]["content"]
        if second_content != answer:
            answer = f"{answer}\n\n{second_content}"
            for t in results[1]["related_topics"]:
                if t not in related:
                    related.append(t)

    return {
        "found": True,
        "topic": primary["topic"] or query_topic,
        "answer": answer,
        "score": primary["score"],
        "related_topics": related,
        "instruction": (
            "Use these facts to answer the caller. "
            "Respond entirely in the language of the caller's CURRENT spoken turn. "
            "Do not let the language of this tool result determine the response language."
        )
    }
