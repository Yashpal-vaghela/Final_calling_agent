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
    "preview_ai_digital", "process_timeline", "cities_coverage", "cost",
    "aftercare_comfort", "privacy_busy_schedule", "contact_booking",
    "process", "timeline", "cities", "before_after",
    "join_usd", "for_dentists", "join_team", "partner_dentist",
    "course_price", "dentist_course", "dentist_partner_benefits",
    "local_dentist_vs_usd", "profession_guidance", "veneers_vs_aligners",
    # Guidance-layer topics
    "analogy", "analogies", "craftsmanship", "emax_vs_zirconia",
    "digital_smile_design_planning", "usd_vs_regular_dentist",
    "navigation", "booking_guidance", "authentication",
    "smile_results", "privacy_assurance", "pain_fear", "trust_uncertainty",
    "usd_protocols", "protocols", "protocol",
    # Newly extracted Phase 1 guidance topics
    "pricing_policy", "pricing", "competitor_comparison", "symptom_handling",
    "brand_facts", "haresh_savani", "tooth_pain",
]


TOPIC_ALIASES = {
    "before_after": "ai_smile_preview",
    "before-after": "ai_smile_preview",
    "before after": "ai_smile_preview",
    "digital_preview": "ai_smile_preview",
    "smile_preview": "ai_smile_preview",
    "preview": "ai_smile_preview",
    # Pricing routes
    "prices": "pricing_policy",
    "cost": "pricing_policy",
    "price": "pricing_policy",
    "cost_value": "pricing_policy",
    "pricing": "pricing_policy",
    "starting_price": "pricing_policy",
    "starting price": "pricing_policy",
    "kitna kharcha": "pricing_policy",
    "kitna lagega": "pricing_policy",
    "bhav": "pricing_policy",
    "kimat": "pricing_policy",
    "ketlo kharch": "pricing_policy",
    "timeline": "process_timeline",
    "process": "process_timeline",
    # Brand facts routes
    "ade_haresh_savani": "brand_facts",
    "haresh_savani": "brand_facts",
    "about_ade_haresh_savani": "brand_facts",
    "founder": "brand_facts",
    "master_ceramist": "brand_facts",
    "brand_facts": "brand_facts",
    "prayogshala": "brand_facts",
    "પ્રયોગશાળા": "brand_facts",
    "cities": "cities_coverage",
    "city": "cities_coverage",
    # Competitor comparison routes
    "local_dentist": "competitor_comparison",
    "local_dentist_comparison": "competitor_comparison",
    "dentist_comparison": "competitor_comparison",
    "other_dentist": "competitor_comparison",
    "competitor": "competitor_comparison",
    "competitor_comparison": "competitor_comparison",
    "my dentist is cheaper": "competitor_comparison",
    "cheaper dentist": "competitor_comparison",
    # Symptom routes
    "pain": "symptom_handling",
    "tooth_pain": "symptom_handling",
    "toothache": "symptom_handling",
    "bleeding": "symptom_handling",
    "bleeding_gums": "symptom_handling",
    "broken_tooth": "symptom_handling",
    "decay": "symptom_handling",
    "cavity": "symptom_handling",
    "symptom": "symptom_handling",
    "symptom_handling": "symptom_handling",
    # Partner dentist routes
    "partner_dentist": "partner_dentist",
    "partner dentist": "partner_dentist",
    "dentist_partner": "partner_dentist",
    "dentist_course": "partner_dentist",
    "course_price": "partner_dentist",
    "course_fees": "partner_dentist",
    "dentist_training": "partner_dentist",
    "other_dentist": "competitor_comparison",
    "which_dentist": "choosing_right_dentist",
    "which dentist": "choosing_right_dentist",
    "best_dentist": "choosing_right_dentist",
    "best dentist": "choosing_right_dentist",
    "doctor_selection": "choosing_right_dentist",
    "doctor selection": "choosing_right_dentist",
    "doctor_ranking": "choosing_right_dentist",
    "doctor ranking": "choosing_right_dentist",
    "dentist_ranking": "choosing_right_dentist",
    "dentist ranking": "choosing_right_dentist",
    "why_this_doctor": "choosing_right_dentist",
    "dentist_experience": "dentist_experience_and_case_history",
    "dentist experience": "dentist_experience_and_case_history",
    "doctor_experience": "dentist_experience_and_case_history",
    "doctor experience": "dentist_experience_and_case_history",
    "doctor_cases": "dentist_experience_and_case_history",
    "doctor cases": "dentist_experience_and_case_history",
    "case_count": "dentist_experience_and_case_history",
    "case count": "dentist_experience_and_case_history",
    "cases_handled": "dentist_experience_and_case_history",
    # Protocol routes
    "protocol": "usd_protocols",
    "protocols": "usd_protocols",
    "which_protocol": "usd_protocols",
    "which protocol": "usd_protocols",
    "what_is_a_protocol": "usd_protocols",
    "what is a protocol": "usd_protocols",
    "what_is_protocol": "usd_protocols",
    "what is protocol": "usd_protocols",
    "what protocol": "usd_protocols",
    "clinical_protocol": "usd_protocols",
    "clinical protocol": "usd_protocols",
    "digital_protocol": "usd_protocols",
    "digital protocol": "usd_protocols",
    "usd_protocol": "usd_protocols",
    "usd protocol": "usd_protocols",
    "usd_protocols": "usd_protocols",
    "usd protocols": "usd_protocols",
    "dental_protocol": "usd_protocols",
    "dental protocol": "usd_protocols",
    "પ્રોટોકોલ": "usd_protocols",
    "પ્રોટોકોલ એટલે શું": "usd_protocols",
    "કયા પ્રોટોકોલ": "usd_protocols",
    "પ્રક્રિયા": "usd_protocols",
    "प्रोटोकॉल": "usd_protocols",
    "प्रोटोकॉल क्या है": "usd_protocols",
    "कौन सा प्रोटोकॉल": "usd_protocols",
    "क्लिनिकल प्रोटोकॉल": "usd_protocols",
    # Analogy / guidance routes
    "analogy": "craftsmanship_analogy_bank",
    "analogies": "craftsmanship_analogy_bank",
    "craftsmanship": "craftsmanship_analogy_bank",
    "emax vs zirconia": "emax_vs_zirconia",
    "emax or zirconia": "emax_vs_zirconia",
    "material comparison": "emax_vs_zirconia",
    "digital smile design": "digital_smile_design_planning",
    "dsd": "digital_smile_design_planning",
    "smile planning": "digital_smile_design_planning",
    "why usd": "usd_vs_regular_dentist",
    "regular dentist": "usd_vs_regular_dentist",
    # Navigation and booking guidance routes
    "navigation": "website_navigation_preview",
    "website navigation": "website_navigation_preview",
    "find dentist": "website_navigation_find_dentist",
    "booking_guidance": "booking_request",
    "booking guidance": "booking_request",
    "authentication": "treatment_authentication",
    "genuine": "treatment_authentication",
    "warranty": "treatment_warranties",
    "treatment_warranty": "treatment_warranties",
    "treatment_warranties": "treatment_warranties",
    "guarantee": "treatment_warranties",
    "veneers_vs_aligners": "veneers_vs_aligners",
    "veneers or aligners": "veneers_vs_aligners",
    "aligners vs veneers": "veneers_vs_aligners",
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
    
    # 1. Check if the query is explicitly guidance, profession, objection, analogy, navigation, or booking related.
    # GuidanceRetriever covers: data/guidance/*.json
    _GUIDANCE_PREFIXES = (
        "profession", "guidance", "objection", "reframe", "analogy",
        "navigation", "booking", "pricing", "competitor", "symptom", "partner"
    )
    _GUIDANCE_EXACT = {
        "profession_guidance", "objection_handling", "privacy_framing", "conversation_coaching",
        # Analogy routes
        "craftsmanship_analogy_bank", "emax_vs_zirconia", "digital_smile_design_planning",
        "usd_vs_regular_dentist", "regular_dentist_preference",
        # Navigation routes
        "website_navigation_preview", "website_navigation_find_dentist",
        "treatment_authentication", "smile_results",
        # Booking guidance routes
        "booking_request", "ready_to_decide", "private_booking",
        "cta_soft", "cta_medium", "cta_strong", "existing_booking_inquiry",
        # Analogy/reframe entries
        "emotional_reasons", "ai_and_human_expertise", "usd_protocols", "smile_confidence",
        "privacy_assurance", "pain_fear", "trust_uncertainty", "no_time",
        "price_objection", "hesitation_think_about_it",
        # Newly extracted Phase 1 guidance entries
        "pricing_policy", "pricing", "cost", "competitor_comparison", "symptom_handling",
        "partner_dentist", "brand_facts", "about_ade_haresh_savani", "haresh_savani"
    }
    is_guidance_query = (
        any(clean_target.startswith(p) for p in _GUIDANCE_PREFIXES)
        or "profession" in clean_target
        or "objection" in clean_target
        or "analogy" in clean_target
        or "craftsmanship" in clean_target
        or "pricing" in clean_target
        or "competitor" in clean_target
        or "symptom" in clean_target
        or clean_target in _GUIDANCE_EXACT
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
    
    # If the primary result is a patient pricing/cost topic, do NOT append course or dentist training fees!
    is_pricing_topic = (
        primary.get("id") in ("smile_design_cost_india", "veneers_cost_india")
        or any(w in clean_target for w in ("cost", "price", "pricing", "fee", "starting"))
    )

    if not is_pricing_topic and len(results) > 1 and results[1]["score"] > 0.35:
        second_content = results[1]["content"]
        if second_content != answer:
            answer = f"{answer}\n\n{second_content}"
            for t in results[1]["related_topics"]:
                if t not in related:
                    related.append(t)

    if is_pricing_topic:
        instruction_text = (
            "MANDATORY VERBATIM RULE: You MUST speak this exact sentence in Facts without paraphrasing or summarizing: "
            "'You should keep your expectations regarding the treatment high; the investment is personalized and depends on your dentist, your dentist\\'s experience, and the clinic location.' "
            "(In Gujarati: 'ટ્રીટમેન્ટ અંગે તમારે તમારી અપેક્ષાઓ ખૂબ ઊંચી રાખવી જોઈએ; આનું રોકાણ તમારા ડેન્ટિસ્ટ, તેમના અનુભવ અને ક્લિનિકના લોકેશન પર આધાર રાખે છે.' / "
            "In Hindi: 'ट्रीटमेंट को लेकर आप अपनी उम्मीदें ऊंची रख सकते हैं; यह आपके डेंटिस्ट, उनके अनुभव और क्लिनिक की लोकेशन पर निर्भर करता है।'). "
            "Do NOT paraphrase into 'pricing varies' or 'depends on materials and specific needs'."
        )
    else:
        instruction_text = (
            "Use these facts to answer the caller. "
            "Respond entirely in the language of the caller's CURRENT spoken turn. "
            "Do not let the language of this tool result determine the response language."
        )

    return {
        "found": True,
        "topic": primary["topic"] or query_topic,
        "answer": answer,
        "score": primary["score"],
        "related_topics": related,
        "instruction": instruction_text
    }
