from __future__ import annotations
import copy
import unicodedata

def filter_messages(result: dict, query: str, limit: int=50) -> dict:
    if not query.strip():
        raise ValueError("QUERY_REQUIRED: 비어 있지 않은 검색어를 지정하세요.")
    if not 1 <= limit <= 1000:
        raise ValueError("INVALID_LIMIT: 1~1000 사이를 지정하세요.")
    needle=unicodedata.normalize("NFKC",query).casefold()
    found=[m for m in result["messages"] if needle in unicodedata.normalize("NFKC",str(m.get("message") or "")).casefold()]
    selected=sorted(found,key=lambda m:(m["sent_at"],m["chat_id"],m["log_id"]),reverse=True)[:limit]
    answer={**result,"summary":copy.deepcopy(result["summary"]),"messages":selected}
    s=answer["summary"]
    s["searched_message_count"]=len(result["messages"])
    s["query"]=query
    s["search_kind"]="normalized_case_insensitive_literal_substring"
    s["matched_message_count"]=len(found)
    s["message_count"]=len(selected)
    s["rooms_with_messages"]=len({m["chat_id"] for m in selected})
    s["received_messages"]=sum(m.get("direction")=="received" for m in selected)
    s["sent_messages"]=sum(m.get("direction")=="sent" for m in selected)
    s["chat_db_messages"]=sum(m.get("source")=="chat_db" for m in selected)
    s["room_list_preview_messages"]=sum(m.get("source")=="room_list_preview" for m in selected)
    s["results_truncated"]=len(found)>len(selected)
    s["result_limit"]=limit
    return answer
