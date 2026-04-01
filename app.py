import base64
import json
import sqlite3
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path

import requests
import streamlit as st

# ===== CONFIG =====
from config import DB_PATH, IMAGES_FOLDER, BACKUPS_FOLDER, LLM_API_KEY, LLM_BASE_URL, LLM_MODEL

THEME_MAP = {
    "Anime/Manga": "AN",
    "City Views": "CT",
    "Art & Heritage": "AR",
    "Landmarks": "LM",
    "Map": "MP",
    "Transport": "TR",
    "Nature": "NA",
    "Food & Culture": "FC",
    "Holidays & Festivals": "HF"
}

BLOCKED_TAGS = {
    "nice",
    "pretty",
    "holiday",
    "asian culture",
    "celebration",
    "tradition",
    "festival celebration",
    "community"
}

STOPWORDS = {
    "the", "and", "or", "of", "in", "on", "at", "for", "with",
    "a", "an", "to", "is", "are"
}


# ===== DB HELPERS =====
def now_kl():
    return datetime.now(ZoneInfo("Asia/Kuala_Lumpur"))

def get_connection():
    return sqlite3.connect(DB_PATH)

import shutil

def backup_database():
    timestamp = now_kl().strftime("%Y%m%d_%H%M%S")
    backup_path = BACKUPS_FOLDER / f"postcards_backup_{timestamp}.db"

    shutil.copy(DB_PATH, backup_path)

    return backup_path

def list_backups():
    return sorted(
        BACKUPS_FOLDER.glob("postcards_backup_*.db"),
        key=lambda p: p.stat().st_mtime,
        reverse=True
    )


def restore_database(backup_file):
    if not backup_file.exists():
        raise FileNotFoundError(f"Backup file not found: {backup_file}")

    # create safety backup before restore
    safety_backup = BACKUPS_FOLDER / f"pre_restore_{now_kl().strftime('%Y%m%d_%H%M%S')}.db"
    shutil.copy(DB_PATH, safety_backup)

    # restore selected backup
    shutil.copy(backup_file, DB_PATH)

def init_db():
    conn = get_connection()
    cur = conn.cursor()

    # Postcards table
    cur.execute("""
    CREATE TABLE IF NOT EXISTS postcards (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        code TEXT UNIQUE,
        description TEXT,
        theme TEXT,
        tags TEXT,
        status TEXT NOT NULL DEFAULT 'in_stock',
        image_path TEXT,
        date_added TEXT DEFAULT CURRENT_TIMESTAMP,
        date_sent TEXT,
        notes TEXT
    )
    """)

    # Request logs table
    cur.execute("""
    CREATE TABLE IF NOT EXISTS request_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        request_text TEXT,
        extracted_preferences TEXT,
        suggested_codes TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )
    """)

    conn.commit()
    conn.close()


def fetch_all_postcards():
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT id, code, description, theme, tags, status, image_path, date_added, date_sent, notes
        FROM postcards
        ORDER BY id DESC
    """)

    rows = cur.fetchall()
    conn.close()
    return rows


def fetch_draft_postcards():
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT id, image_path, notes, date_added
        FROM postcards
        WHERE status = 'draft'
        ORDER BY id DESC
    """)

    rows = cur.fetchall()
    conn.close()
    return rows


def get_postcard_by_id(postcard_id):
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT id, code, description, theme, tags, status, image_path, notes, date_sent
        FROM postcards
        WHERE id = ?
    """, (postcard_id,))

    row = cur.fetchone()
    conn.close()

    if row is None:
        return None

    return {
        "id": row[0],
        "code": row[1],
        "description": row[2],
        "theme": row[3],
        "tags": row[4],
        "status": row[5],
        "image_path": row[6],
        "notes": row[7],
        "date_sent": row[8],
    }


def get_postcard_by_code(code):
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT id, code, description, theme, tags, status, image_path, notes, date_sent
        FROM postcards
        WHERE code = ?
    """, (code,))

    row = cur.fetchone()
    conn.close()

    if row is None:
        return None

    return {
        "id": row[0],
        "code": row[1],
        "description": row[2],
        "theme": row[3],
        "tags": row[4],
        "status": row[5],
        "image_path": row[6],
        "notes": row[7],
        "date_sent": row[8],
    }


def delete_postcard_by_id(postcard_id):
    postcard = get_postcard_by_id(postcard_id)
    if not postcard:
        raise ValueError("Postcard not found.")

    image_path = postcard["image_path"]

    conn = get_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM postcards WHERE id = ?", (postcard_id,))
    conn.commit()
    conn.close()

    if image_path:
        path_obj = Path(image_path)
        if path_obj.exists():
            try:
                path_obj.unlink()
            except Exception:
                pass


def add_image_draft(uploaded_file):
    unique_name = f"{uuid.uuid4().hex}_{uploaded_file.name}"
    image_path = IMAGES_FOLDER / unique_name

    with open(image_path, "wb") as f:
        f.write(uploaded_file.getbuffer())

    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO postcards (code, description, theme, tags, status, image_path, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (
        None,
        None,
        None,
        None,
        "draft",
        str(image_path),
        "Draft created from UI upload"
    ))

    conn.commit()
    postcard_id = cur.lastrowid
    conn.close()

    return postcard_id, str(image_path)


def generate_next_code(theme_abbr):
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT code
        FROM postcards
        WHERE code LIKE ?
          AND code IS NOT NULL
        ORDER BY code DESC
        LIMIT 1
    """, (f"{theme_abbr}%",))

    row = cur.fetchone()
    conn.close()

    if row is None:
        return f"{theme_abbr}001"

    last_code = row[0]
    last_number = int(last_code[-3:])
    next_number = last_number + 1
    return f"{theme_abbr}{next_number:03d}"


def clean_tags(tags):
    cleaned = []

    for tag in tags:
        if not isinstance(tag, str):
            continue

        t = tag.strip().lower()
        if not t:
            continue
        if t in BLOCKED_TAGS:
            continue
        if t not in cleaned:
            cleaned.append(t)

    return cleaned


def update_postcard_from_analysis(postcard_id, description, theme, tags):
    if theme not in THEME_MAP:
        raise ValueError(f"Invalid theme: {theme}")

    theme_abbr = THEME_MAP[theme]
    code = generate_next_code(theme_abbr)
    tags_text = "; ".join(tags)

    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
        UPDATE postcards
        SET code = ?,
            description = ?,
            theme = ?,
            tags = ?,
            status = ?,
            notes = ?,
            date_added = CURRENT_TIMESTAMP
        WHERE id = ?
    """, (
        code,
        description,
        theme,
        tags_text,
        "in_stock",
        "Updated by AI analysis in UI",
        postcard_id
    ))

    conn.commit()
    conn.close()

    return code

def update_postcard_fields(postcard_id, description, theme, tags, status, notes):
    if theme not in THEME_MAP:
        raise ValueError(f"Invalid theme: {theme}")

    tags_text = "; ".join(clean_tags([tag.strip() for tag in tags.split(";")])) if tags else ""

    conn = get_connection()
    cur = conn.cursor()

    today = now_kl().strftime("%Y-%m-%d") if status == "sent" else None

    cur.execute("""
        UPDATE postcards
        SET description = ?,
            theme = ?,
            tags = ?,
            status = ?,
            notes = ?,
            date_sent = CASE 
                WHEN ? = 'sent' THEN ?
                ELSE NULL
            END
        WHERE id = ?
    """, (
        description.strip() if description else "",
        theme,
        tags_text,
        status,
        notes.strip() if notes else "",
        status,
        today,
        postcard_id
    ))

    conn.commit()
    conn.close()
    

def mark_sent(code, notes=None):
    postcard = get_postcard_by_code(code)

    if not postcard:
        raise ValueError("Postcard code not found.")

    if postcard["status"] == "sent":
        raise ValueError(f"Postcard already marked as sent on {postcard['date_sent']}.")

    today = now_kl().strftime("%Y-%m-%d")

    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
        UPDATE postcards
        SET status = ?,
            date_sent = ?,
            notes = ?
        WHERE code = ?
    """, ("sent", today, notes, code))

    conn.commit()
    conn.close()

    return today


def mark_sent_by_recommendation(code):
    postcard = get_postcard_by_code(code)

    if not postcard:
        raise ValueError("Postcard code not found.")

    if postcard["status"] == "sent":
        raise ValueError(f"Postcard already marked as sent on {postcard['date_sent']}.")

    today = now_kl().strftime("%Y-%m-%d")

    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
        UPDATE postcards
        SET status = ?, date_sent = ?
        WHERE code = ?
    """, ("sent", today, code))

    conn.commit()
    conn.close()

    return today


def fetch_in_stock_postcards():
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT id, code, description, theme, tags, status, image_path, notes
        FROM postcards
        WHERE status = 'in_stock'
          AND code IS NOT NULL
        ORDER BY code
    """)

    rows = cur.fetchall()
    conn.close()

    postcards = []
    for row in rows:
        postcards.append({
            "id": row[0],
            "code": row[1],
            "description": row[2] or "",
            "theme": row[3] or "",
            "tags": row[4] or "",
            "status": row[5] or "",
            "image_path": row[6] or "",
            "notes": row[7] or "",
        })

    return postcards


def fetch_theme_stats():
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT
            theme,
            COUNT(*) AS total,
            SUM(CASE WHEN status = 'in_stock' THEN 1 ELSE 0 END) AS in_stock,
            SUM(CASE WHEN status = 'sent' THEN 1 ELSE 0 END) AS sent,
            SUM(CASE WHEN status = 'draft' THEN 1 ELSE 0 END) AS draft,
            SUM(CASE WHEN status = 'reserved' THEN 1 ELSE 0 END) AS reserved,
            SUM(CASE WHEN status = 'given_away' THEN 1 ELSE 0 END) AS given_away
        FROM postcards
        WHERE theme IS NOT NULL
        GROUP BY theme
        ORDER BY theme
    """)

    rows = cur.fetchall()
    conn.close()
    return rows


def log_request(profile_text, preferences, reranked):
    conn = get_connection()
    cur = conn.cursor()

    suggested_codes = [item["code"] for item in reranked] if reranked else []

    cur.execute("""
        INSERT INTO request_logs (request_text, extracted_preferences, suggested_codes)
        VALUES (?, ?, ?)
    """, (
        profile_text,
        json.dumps(preferences, ensure_ascii=False),
        json.dumps(suggested_codes, ensure_ascii=False)
    ))

    conn.commit()
    conn.close()


def get_request_theme_signals():
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT extracted_preferences
        FROM request_logs
    """)

    rows = cur.fetchall()
    conn.close()

    theme_keywords = {
        "Anime/Manga": ["anime", "manga", "doraemon", "ghibli", "naruto", "one piece", "harry potter", "marvel", "dc"],
        "City Views": ["city", "skyline", "urban", "street", "buildings"],
        "Art & Heritage": ["art", "heritage", "museum", "painting", "illustration", "traditional", "sherlock holmes", "alice in wonderland"],
        "Landmarks": ["landmark", "lighthouse", "tower", "monument", "bridge"],
        "Map": ["map", "atlas", "geography"],
        "Transport": ["train", "car", "bus", "bicycle", "bike", "transport", "tram", "railway", "basketball players", "basketball", "nba", "stephen curry", "lebron james", "nikola jokic", "nicola jokich"],
        "Nature": ["nature", "flower", "flowers", "forest", "mountain", "sea", "ocean", "tree", "feathers", "coconut", "cats", "dogs", "scottish fold"],
        "Food & Culture": ["food", "culture", "cuisine", "meal", "festival food", "dish", "arabic things", "books"],
        "Holidays & Festivals": ["holiday", "festival", "christmas", "new year", "cny", "celebration", "halloween", "dream catchers", "beauty and the beast", "phantom of the opera"]
    }

    request_counts = {theme: 0 for theme in THEME_MAP.keys()}

    for row in rows:
        extracted_json = row[0]
        if not extracted_json:
            continue

        try:
            prefs = json.loads(extracted_json)
        except Exception:
            continue

        combined_items = []
        for key in [
            "strong_preferences",
            "soft_preferences",
            "themes_or_objects",
            "fandoms_or_named_entities",
            "animals"
        ]:
            values = prefs.get(key, [])
            if isinstance(values, list):
                combined_items.extend([str(v).lower() for v in values])

        combined_text = " ".join(combined_items)

        for theme, keywords in theme_keywords.items():
            for kw in keywords:
                if kw in combined_text:
                    request_counts[theme] += 1
                    break

    return request_counts


def get_restock_recommendations():
    stats = fetch_theme_stats()
    request_counts = get_request_theme_signals()

    recommendations = []

    for row in stats:
        theme, total, in_stock, sent, draft, reserved, given_away = row

        total = total or 0
        in_stock = in_stock or 0
        sent = sent or 0

        requests_count = request_counts.get(theme, 0)

        if in_stock <= 1:
            stock_score = 5
        elif in_stock <= 3:
            stock_score = 4
        elif in_stock <= 5:
            stock_score = 3
        elif in_stock <= 8:
            stock_score = 2
        else:
            stock_score = 1

        if sent >= 10:
            sent_score = 5
        elif sent >= 7:
            sent_score = 4
        elif sent >= 4:
            sent_score = 3
        elif sent >= 2:
            sent_score = 2
        elif sent >= 1:
            sent_score = 1
        else:
            sent_score = 0

        if requests_count >= 10:
            request_score = 5
        elif requests_count >= 7:
            request_score = 4
        elif requests_count >= 4:
            request_score = 3
        elif requests_count >= 2:
            request_score = 2
        elif requests_count >= 1:
            request_score = 1
        else:
            request_score = 0

        priority_score = stock_score + sent_score + request_score

        base_target = 5
        demand_boost = min(3, requests_count) + min(3, sent // 3)
        target_stock = base_target + demand_boost
        restock_qty = max(0, target_stock - in_stock)

        recommendations.append({
            "theme": theme,
            "in_stock": in_stock,
            "sent": sent,
            "requests": requests_count,
            "priority_score": priority_score,
            "target_stock": target_stock,
            "restock_qty": restock_qty
        })

    recommendations.sort(
        key=lambda x: (-x["priority_score"], -x["restock_qty"], x["in_stock"], -x["sent"], -x["requests"])
    )

    return recommendations


# ===== AI HELPERS: IMAGE ANALYSIS =====
def encode_image_to_base64(image_path):
    with open(image_path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


def analyze_image_with_llm(image_path):
    if not LLM_API_KEY:
        raise ValueError("Missing LLM_API_KEY environment variable.")
    if not LLM_BASE_URL:
        raise ValueError("Missing LLM_BASE_URL environment variable.")
    if not LLM_MODEL:
        raise ValueError("Missing LLM_MODEL environment variable.")

    image_b64 = encode_image_to_base64(image_path)
    allowed_themes = list(THEME_MAP.keys())

    prompt = f"""
You are analyzing a postcard image for a postcard inventory database used for Postcrossing.

Return ONLY valid JSON with this exact structure:
{{
  "options": [
    {{
      "theme": "One exact theme from the allowed list",
      "description": "One concise but informative sentence aligned to that theme",
      "tags": ["tag1", "tag2", "tag3"]
    }},
    {{
      "theme": "One exact theme from the allowed list",
      "description": "One concise but informative sentence aligned to that theme",
      "tags": ["tag1", "tag2", "tag3"]
    }},
    {{
      "theme": "One exact theme from the allowed list",
      "description": "One concise but informative sentence aligned to that theme",
      "tags": ["tag1", "tag2", "tag3"]
    }}
  ]
}}

Allowed themes:
{json.dumps(allowed_themes, ensure_ascii=False)}

Critical rules:
1. Provide EXACTLY 3 options.
2. The 3 options must use 3 DIFFERENT themes.
3. Each theme must be chosen from the allowed themes only.
4. Each option must represent a DIFFERENT plausible interpretation of the same image.
5. The description and tags for each option must support that option's chosen theme.
6. Do not give 3 versions of the same idea with different wording.
7. Prefer strong visual evidence from the image.

Tag rules:
1. Tags must be lowercase.
2. Prefer specific tags over generic tags.
3. Prefer 8 to 12 tags per option.
4. No duplicate tags inside an option.
5. Avoid weak tags like:
   - nice
   - pretty
   - holiday
   - asian culture
   - celebration
   - tradition

Description rules:
1. One sentence only.
2. Be concrete and visually grounded.
3. Emphasize the elements most relevant to the chosen theme.

Do not include markdown.
Do not include explanation outside JSON.
"""

    payload = {
        "model": LLM_MODEL,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/jpeg;base64,{image_b64}"
                        }
                    }
                ]
            }
        ],
        "temperature": 0.8
    }

    headers = {
        "Authorization": f"Bearer {LLM_API_KEY}",
        "Content-Type": "application/json"
    }

    response = requests.post(
        f"{LLM_BASE_URL}/chat/completions",
        headers=headers,
        json=payload,
        timeout=120
    )
    response.raise_for_status()

    data = response.json()
    content = data["choices"][0]["message"]["content"]
    result = json.loads(content)

    options = result.get("options")
    if not isinstance(options, list) or len(options) != 3:
        raise ValueError("LLM did not return exactly 3 options.")

    parsed = []

    for opt in options:
        description = opt.get("description")
        theme = opt.get("theme")
        tags = opt.get("tags")

        if not description or not isinstance(description, str):
            continue
        if theme not in THEME_MAP:
            continue
        if not isinstance(tags, list):
            continue

        cleaned_tags = clean_tags(tags)

        parsed.append({
            "theme": theme,
            "description": description.strip(),
            "tags": cleaned_tags
        })

    if len(parsed) != 3:
        raise ValueError("LLM did not return 3 valid options.")

    theme_set = {opt["theme"] for opt in parsed}
    if len(theme_set) != 3:
        raise ValueError("LLM returned duplicate themes. Please analyze again.")

    return parsed


# ===== AI HELPERS: SMART SUGGEST =====
def extract_preferences_with_ai(profile_text):
    if not LLM_API_KEY:
        raise ValueError("Missing LLM_API_KEY environment variable.")
    if not LLM_BASE_URL:
        raise ValueError("Missing LLM_BASE_URL environment variable.")
    if not LLM_MODEL:
        raise ValueError("Missing LLM_MODEL environment variable.")

    prompt = """
You are extracting postcard preferences from a Postcrossing user profile.

Return ONLY valid JSON with this exact structure:
{
  "strong_preferences": ["..."],
  "soft_preferences": ["..."],
  "themes_or_objects": ["..."],
  "fandoms_or_named_entities": ["..."],
  "animals": ["..."],
  "ignore": ["..."]
}

Rules:
1. Extract only meaningful postcard preferences or useful personal interests.
2. Put highly explicit postcard wishes into strong_preferences.
3. Put softer background interests into soft_preferences.
4. Put concrete postcardable objects/themes into themes_or_objects.
5. Put specific fandoms, titles, celebrities, or named entities into fandoms_or_named_entities.
6. Put animals into animals.
7. Put filler or irrelevant text into ignore.
8. Keep all values lowercase.
9. Remove duplicates.
10. Do not include markdown.
11. Do not include explanation outside JSON.
"""

    payload = {
        "model": LLM_MODEL,
        "messages": [
            {
                "role": "user",
                "content": f"{prompt}\n\nPROFILE:\n{profile_text}"
            }
        ],
        "temperature": 0.2
    }

    headers = {
        "Authorization": f"Bearer {LLM_API_KEY}",
        "Content-Type": "application/json"
    }

    response = requests.post(
        f"{LLM_BASE_URL}/chat/completions",
        headers=headers,
        json=payload,
        timeout=120
    )
    response.raise_for_status()

    data = response.json()
    content = data["choices"][0]["message"]["content"]
    result = json.loads(content)

    expected_keys = [
        "strong_preferences",
        "soft_preferences",
        "themes_or_objects",
        "fandoms_or_named_entities",
        "animals",
        "ignore"
    ]

    cleaned = {}

    for key in expected_keys:
        value = result.get(key, [])
        if not isinstance(value, list):
            value = []

        deduped = []
        for item in value:
            if isinstance(item, str):
                t = item.strip().lower()
                if t and t not in deduped:
                    deduped.append(t)

        cleaned[key] = deduped

    return cleaned


def normalize_text(text):
    if not text:
        return ""

    text = text.lower()
    separators = [",", ";", ".", "/", "-", "_", "(", ")", ":", "!", "?", "*"]
    for sep in separators:
        text = text.replace(sep, " ")

    return " ".join(text.split())


def field_match_score(field_text, phrase):
    field_text = normalize_text(field_text)
    phrase = normalize_text(phrase)

    if not field_text or not phrase:
        return 0

    phrase_words = [w for w in phrase.split() if w not in STOPWORDS]

    if not phrase_words:
        return 0

    cleaned_phrase = " ".join(phrase_words)

    if cleaned_phrase in field_text:
        return 3

    match_count = 0
    for word in phrase_words:
        if word in field_text:
            match_count += 1

    if match_count == 0:
        return 0

    ratio = match_count / len(phrase_words)

    if ratio == 1:
        return 2
    elif ratio >= 0.5:
        return 1
    else:
        return 0


def score_postcard_smart(postcard, preferences):
    theme_text = postcard["theme"]
    tags_text = postcard["tags"]
    description_text = postcard["description"]
    notes_text = postcard["notes"]

    score = 0
    reasons = []

    for item in preferences["strong_preferences"]:
        m = field_match_score(tags_text, item)
        if m:
            score += 10 * m
            reasons.append(f"strong tag match: {item}")
            continue

        m = field_match_score(description_text, item)
        if m:
            score += 8 * m
            reasons.append(f"strong description match: {item}")
            continue

        m = field_match_score(theme_text, item)
        if m:
            score += 6 * m
            reasons.append(f"strong theme match: {item}")

    for item in preferences["fandoms_or_named_entities"]:
        m = field_match_score(tags_text, item)
        if m:
            score += 10 * m
            reasons.append(f"fandom tag match: {item}")
            continue

        m = field_match_score(description_text, item)
        if m:
            score += 8 * m
            reasons.append(f"fandom description match: {item}")

    for item in preferences["themes_or_objects"]:
        m = field_match_score(tags_text, item)
        if m:
            score += 7 * m
            reasons.append(f"object tag match: {item}")
            continue

        m = field_match_score(description_text, item)
        if m:
            score += 5 * m
            reasons.append(f"object description match: {item}")
            continue

        m = field_match_score(theme_text, item)
        if m:
            score += 4 * m
            reasons.append(f"object theme match: {item}")

    for item in preferences["animals"]:
        m = field_match_score(tags_text, item)
        if m:
            score += 8 * m
            reasons.append(f"animal tag match: {item}")
            continue

        m = field_match_score(description_text, item)
        if m:
            score += 6 * m
            reasons.append(f"animal description match: {item}")

    for item in preferences["soft_preferences"]:
        m = field_match_score(tags_text, item)
        if m:
            score += 4 * m
            reasons.append(f"soft tag match: {item}")
            continue

        m = field_match_score(description_text, item)
        if m:
            score += 3 * m
            reasons.append(f"soft description match: {item}")
            continue

        m = field_match_score(notes_text, item)
        if m:
            score += 2 * m
            reasons.append(f"soft notes match: {item}")

    unique_concepts = set()
    for reason in reasons:
        if ": " in reason:
            unique_concepts.add(reason.split(": ", 1)[1])

    score += min(len(unique_concepts), 5)

    deduped_reasons = []
    for reason in reasons:
        if reason not in deduped_reasons:
            deduped_reasons.append(reason)

    return score, deduped_reasons


def build_smart_shortlist(profile_text, limit=10):
    preferences = extract_preferences_with_ai(profile_text)
    postcards = fetch_in_stock_postcards()

    scored_results = []

    for postcard in postcards:
        score, reasons = score_postcard_smart(postcard, preferences)

        scored_results.append({
            "postcard": postcard,
            "score": score,
            "reasons": reasons if reasons else ["no direct match"]
        })

    scored_results.sort(key=lambda x: (-x["score"], x["postcard"]["code"]))
    return preferences, scored_results[:limit]


def rerank_shortlist_with_ai(profile_text, preferences, shortlist):
    if not shortlist:
        return []

    shortlist_payload = []
    for item in shortlist:
        postcard = item["postcard"]
        shortlist_payload.append({
            "code": postcard["code"],
            "theme": postcard["theme"],
            "description": postcard["description"],
            "tags": postcard["tags"],
            "local_score": item["score"],
            "local_reasons": item["reasons"]
        })

    prompt = """
You are helping choose the best postcard for a Postcrossing recipient.

Return ONLY valid JSON with this exact structure:
{
  "top_choices": [
    {
      "code": "FC001",
      "rank": 1,
      "reason": "short reason why this postcard fits best"
    },
    {
      "code": "AN003",
      "rank": 2,
      "reason": "short reason why this postcard fits well"
    },
    {
      "code": "AR004",
      "rank": 3,
      "reason": "short reason why this postcard is a weaker but still good option"
    }
  ]
}

Rules:
1. Choose EXACTLY 3 postcards if possible. If fewer than 3 valid options exist, return as many as possible.
2. Use only postcard codes from the provided shortlist.
3. Consider the receiver profile, extracted preferences, and postcard metadata together.
4. Prefer postcards that best match explicit interests and named entities.
5. Avoid generic reasons. Mention specific matched interests when possible.
6. If exact fandom/object matches are unavailable, choose the closest aesthetically or thematically suitable cards from the shortlist.
7. Do not falsely claim an exact match when there is none.
8. Do not include markdown.
9. Do not include explanation outside JSON.
"""

    payload = {
        "model": LLM_MODEL,
        "messages": [
            {
                "role": "user",
                "content": (
                    f"{prompt}\n\n"
                    f"PROFILE:\n{profile_text}\n\n"
                    f"EXTRACTED_PREFERENCES:\n{json.dumps(preferences, ensure_ascii=False, indent=2)}\n\n"
                    f"SHORTLIST:\n{json.dumps(shortlist_payload, ensure_ascii=False, indent=2)}"
                )
            }
        ],
        "temperature": 0.3
    }

    headers = {
        "Authorization": f"Bearer {LLM_API_KEY}",
        "Content-Type": "application/json"
    }

    response = requests.post(
        f"{LLM_BASE_URL}/chat/completions",
        headers=headers,
        json=payload,
        timeout=120
    )
    response.raise_for_status()

    data = response.json()
    content = data["choices"][0]["message"]["content"]
    result = json.loads(content)

    top_choices = result.get("top_choices", [])
    if not isinstance(top_choices, list):
        return []

    valid_codes = {item["postcard"]["code"] for item in shortlist}
    cleaned = []
    seen_codes = set()

    for item in top_choices:
        if not isinstance(item, dict):
            continue

        code = item.get("code")
        rank = item.get("rank")
        reason = item.get("reason")

        if code not in valid_codes:
            continue
        if code in seen_codes:
            continue
        if not isinstance(reason, str) or not reason.strip():
            continue
        if not isinstance(rank, int):
            continue

        cleaned.append({
            "code": code,
            "rank": rank,
            "reason": reason.strip()
        })
        seen_codes.add(code)

    cleaned.sort(key=lambda x: x["rank"])
    return cleaned


# ===== INIT =====
init_db()

# ===== UI =====
st.set_page_config(page_title="Postcrossing Assistant", layout="wide")
st.title("📮 Postcrossing Assistant")

tabs = st.tabs(["Upload & Analyze", "Smart Suggest", "Mark Sent", "Stock", "All Postcards"])

# ===== TAB 1 =====
with tabs[0]:
    st.header("Upload and Analyze Postcard")

    uploaded_file = st.file_uploader("Upload postcard image", type=["jpg", "jpeg", "png"])

    if uploaded_file is not None:
        st.image(uploaded_file, caption=uploaded_file.name, width=350)

        if st.button("Save as Draft"):
            try:
                postcard_id, saved_path = add_image_draft(uploaded_file)
                st.success(f"Draft saved. Postcard ID: {postcard_id}")
                st.info(saved_path)
            except Exception as e:
                st.error(str(e))

    st.subheader("Analyze Existing Draft")

    drafts = fetch_draft_postcards()
    draft_options = {
        f"ID {row[0]} | {Path(row[1]).name}": row[0]
        for row in drafts
    }

    if draft_options:
        selected_label = st.selectbox("Select a draft postcard", list(draft_options.keys()))
        selected_id = draft_options[selected_label]
        postcard = get_postcard_by_id(selected_id)

        if postcard and postcard["image_path"] and Path(postcard["image_path"]).exists():
            st.image(postcard["image_path"], caption=f"Draft ID {selected_id}", width=350)

        col1, col2 = st.columns(2)

        with col1:
            if st.button("Analyze Draft with AI"):
                try:
                    options = analyze_image_with_llm(Path(postcard["image_path"]))
                    st.session_state["analysis_options"] = options
                    st.session_state["analysis_postcard_id"] = selected_id
                    st.success("Analysis complete.")
                except Exception as e:
                    st.error(str(e))

        with col2:
            if st.button(f"Delete Draft ID {selected_id}"):
                try:
                    delete_postcard_by_id(selected_id)
                    if st.session_state.get("analysis_postcard_id") == selected_id:
                        st.session_state.pop("analysis_options", None)
                        st.session_state.pop("analysis_postcard_id", None)
                    st.success("Draft deleted. Refresh or interact again.")
                    st.rerun()
                except Exception as e:
                    st.error(str(e))

        if "analysis_options" in st.session_state and st.session_state.get("analysis_postcard_id") == selected_id:
            options = st.session_state["analysis_options"]

            st.subheader("Choose One Option")

            labels = [f"Option {i} - {opt['theme']}" for i, opt in enumerate(options, start=1)]
            chosen_label = st.radio("AI options", labels)
            chosen_index = labels.index(chosen_label)
            selected = options[chosen_index]

            edited_theme = st.selectbox(
                "Theme",
                list(THEME_MAP.keys()),
                index=list(THEME_MAP.keys()).index(selected["theme"])
            )
            edited_description = st.text_area("Description", value=selected["description"], height=100)
            edited_tags = st.text_area("Tags (; separated)", value="; ".join(selected["tags"]), height=100)

            if st.button("Save Selected Option to Database"):
                try:
                    final_tags = clean_tags([tag.strip() for tag in edited_tags.split(";")])
                    code = update_postcard_from_analysis(
                        postcard_id=selected_id,
                        description=edited_description.strip(),
                        theme=edited_theme,
                        tags=final_tags
                    )
                    st.success(f"Saved successfully with code: {code}")
                    del st.session_state["analysis_options"]
                    del st.session_state["analysis_postcard_id"]
                    st.rerun()
                except Exception as e:
                    st.error(str(e))
    else:
        st.info("No draft postcards found.")

# ===== TAB 2 =====
with tabs[1]:
    st.header("Smart Suggest Postcards")

    profile_text = st.text_area(
        "Paste full receiver profile or request",
        height=220,
        placeholder="Paste the full Postcrossing profile here..."
    )

    if st.button("Smart Suggest"):
        if not profile_text.strip():
            st.warning("Please paste a receiver profile first.")
        else:
            try:
                preferences, shortlist = build_smart_shortlist(profile_text, limit=10)
                reranked = rerank_shortlist_with_ai(profile_text, preferences, shortlist)
                log_request(profile_text, preferences, reranked)

                st.session_state["smart_profile_text"] = profile_text
                st.session_state["smart_preferences"] = preferences
                st.session_state["smart_shortlist"] = shortlist
                st.session_state["smart_reranked"] = reranked

            except Exception as e:
                st.error(str(e))

    if "smart_preferences" in st.session_state and "smart_shortlist" in st.session_state:
        preferences = st.session_state["smart_preferences"]
        shortlist = st.session_state["smart_shortlist"]
        reranked = st.session_state.get("smart_reranked", [])

        st.subheader("Extracted Preferences")
        st.json(preferences)

        st.subheader("Local Shortlist")
        for i, item in enumerate(shortlist, start=1):
            postcard = item["postcard"]

            st.markdown(f"### Shortlist {i}: {postcard['code']}")
            if postcard["image_path"] and Path(postcard["image_path"]).exists():
                st.image(postcard["image_path"], width=220)

            st.write(f"**Theme:** {postcard['theme']}")
            st.write(f"**Description:** {postcard['description']}")
            st.write(f"**Tags:** {postcard['tags']}")
            st.write(f"**Local Score:** {item['score']}")
            st.write("**Reasons:**")
            for reason in item["reasons"]:
                st.write(f"- {reason}")
            st.divider()

        st.subheader("AI Top Picks")

        if not reranked:
            st.info("No AI reranked results returned.")
        else:
            shortlist_lookup = {item["postcard"]["code"]: item["postcard"] for item in shortlist}

            for item in reranked:
                postcard = shortlist_lookup[item["code"]]

                st.markdown(f"## Rank {item['rank']}: {postcard['code']}")
                if postcard["image_path"] and Path(postcard["image_path"]).exists():
                    st.image(postcard["image_path"], width=260)

                st.write(f"**Theme:** {postcard['theme']}")
                st.write(f"**Description:** {postcard['description']}")
                st.write(f"**Tags:** {postcard['tags']}")
                st.write(f"**Why it fits:** {item['reason']}")

                rec_notes_key = f"rec_notes_{postcard['code']}"
                send_button_key = f"send_btn_{postcard['code']}"
                quick_send_button_key = f"quick_send_btn_{postcard['code']}"

                rec_notes = st.text_area(
                    f"Optional notes for {postcard['code']}",
                    placeholder="recipient name, country, preferences, remarks",
                    key=rec_notes_key,
                    height=80
                )

                col_a, col_b = st.columns(2)

                with col_a:
                    if st.button(f"Mark {postcard['code']} as Sent", key=send_button_key):
                        try:
                            sent_date = mark_sent(
                                postcard["code"],
                                rec_notes.strip() if rec_notes.strip() else None
                            )
                            st.success(f"{postcard['code']} marked as sent on {sent_date}.")
                            st.rerun()
                        except Exception as e:
                            st.error(str(e))

                with col_b:
                    if st.button(f"Quick Send {postcard['code']}", key=quick_send_button_key):
                        try:
                            sent_date = mark_sent_by_recommendation(postcard["code"])
                            st.success(f"{postcard['code']} marked as sent on {sent_date}.")
                            st.rerun()
                        except Exception as e:
                            st.error(str(e))

                st.divider()

# ===== TAB 3 =====
with tabs[2]:
    st.header("Mark Postcard as Sent")

    send_code = st.text_input("Postcard code", placeholder="e.g. FC001")
    send_notes = st.text_area("Optional notes", placeholder="recipient preferences, country, remarks", height=100)

    if send_code.strip():
        postcard_preview = get_postcard_by_code(send_code.upper().strip())
        if postcard_preview:
            st.write(f"**Theme:** {postcard_preview['theme']}")
            st.write(f"**Description:** {postcard_preview['description']}")
            st.write(f"**Tags:** {postcard_preview['tags']}")
            st.write(f"**Current Status:** {postcard_preview['status']}")
            if postcard_preview["image_path"] and Path(postcard_preview["image_path"]).exists():
                st.image(postcard_preview["image_path"], width=220)

    if st.button("Mark as Sent"):
        try:
            sent_date = mark_sent(
                send_code.upper().strip(),
                send_notes.strip() if send_notes.strip() else None
            )
            st.success(f"{send_code.upper().strip()} marked as sent on {sent_date}.")
            st.rerun()
        except Exception as e:
            st.error(str(e))

# ===== TAB 4 =====
with tabs[3]:
    st.header("Stock")

    col_backup1, col_backup2 = st.columns([1, 2])

    with col_backup1:
        if st.button("💾 Backup Database"):
            try:
                backup_path = backup_database()
                st.success(f"Backup created: {backup_path.name}")
                st.caption(str(backup_path))
            except Exception as e:
                st.error(str(e))

        if st.button("📂 Open Backups Folder"):
            import os
            os.startfile(BACKUPS_FOLDER)

    with col_backup2:
        st.caption("Creates a timestamped backup of your database.")

        backups = list_backups()

        if not backups:
            st.info("No backups found yet.")
        else:
            selected_backup_name = st.selectbox(
                "Select backup to restore",
                [b.name for b in backups]
            )

            selected_backup = next(b for b in backups if b.name == selected_backup_name)

            confirm_restore = st.checkbox(
                "I understand this will overwrite the current database.",
                key="confirm_restore_db"
            )

            if st.button("♻️ Restore Selected Backup"):
                if not confirm_restore:
                    st.warning("Please confirm restore first.")
                else:
                    try:
                        restore_database(selected_backup)
                        st.success(f"Restored from: {selected_backup.name}")
                        st.rerun()
                    except Exception as e:
                        st.error(str(e))

    stock_subtabs = st.tabs(["Restock Suggestions", "Theme Stocks", "Request Signals"])

    with stock_subtabs[0]:
        st.subheader("Restock Suggestions")
        st.caption("Priority is based on stock left, sent count, and request signals.")

        recommendations = get_restock_recommendations()

        if not recommendations:
            st.info("No restock data available yet.")
        else:
            top_n = st.slider("How many themes to show", min_value=3, max_value=10, value=5, key="restock_top_n")

            for rec in recommendations[:top_n]:
                urgency = "Low"
                if rec["priority_score"] >= 10:
                    urgency = "High"
                elif rec["priority_score"] >= 7:
                    urgency = "Medium"

                with st.container(border=True):
                    col1, col2, col3, col4, col5 = st.columns([2, 1, 1, 1, 1])

                    with col1:
                        st.markdown(f"### {rec['theme']}")
                        st.write(f"**Urgency:** {urgency}")
                    with col2:
                        st.metric("In Stock", rec["in_stock"])
                    with col3:
                        st.metric("Sent", rec["sent"])
                    with col4:
                        st.metric("Signals", rec["requests"])
                    with col5:
                        st.metric("Restock Qty", rec["restock_qty"])

                    st.write(f"**Target Stock Level:** {rec['target_stock']}")
                    st.progress(min(rec["priority_score"] / 15, 1.0), text=f"Priority Score: {rec['priority_score']}")

                    if rec["restock_qty"] <= 0:
                        st.caption("No immediate restock needed.")
                    elif rec["priority_score"] >= 10:
                        st.warning(f"Restock soon: add about {rec['restock_qty']} cards.")
                    elif rec["priority_score"] >= 7:
                        st.info(f"Recommended restock: add about {rec['restock_qty']} cards.")
                    else:
                        st.caption(f"Low urgency: add about {rec['restock_qty']} cards when convenient.")

    with stock_subtabs[1]:
        st.subheader("Theme Stocks")

        stats = fetch_theme_stats()

        if not stats:
            st.info("No postcard data found.")
        else:
            for row in stats:
                theme, total, in_stock, sent, draft, reserved, given_away = row

                total = total or 0
                in_stock = in_stock or 0
                sent = sent or 0
                draft = draft or 0
                reserved = reserved or 0
                given_away = given_away or 0

                usage_rate = (sent / total * 100) if total > 0 else 0

                with st.expander(f"{theme}  |  In Stock: {in_stock}  |  Sent: {sent}", expanded=False):
                    col1, col2, col3 = st.columns(3)

                    with col1:
                        st.metric("Total", total)
                        st.metric("In Stock", in_stock)
                    with col2:
                        st.metric("Sent", sent)
                        st.metric("Draft", draft)
                    with col3:
                        st.metric("Reserved", reserved)
                        st.metric("Given Away", given_away)

                    st.progress(min(usage_rate / 100, 1.0), text=f"Usage Rate: {usage_rate:.1f}%")

    with stock_subtabs[2]:
        st.subheader("Request Signals")
        st.caption("How often recent receiver requests point toward each theme.")

        request_counts = get_request_theme_signals()

        if not request_counts:
            st.info("No request signals available yet.")
        else:
            sorted_requests = sorted(request_counts.items(), key=lambda x: (-x[1], x[0]))

            for theme, count in sorted_requests:
                with st.container(border=True):
                    col1, col2 = st.columns([3, 1])
                    with col1:
                        st.write(f"**{theme}**")
                    with col2:
                        st.metric("Signals", count)

# ===== TAB 5 =====
with tabs[4]:
    st.header("All Postcards")

    rows = fetch_all_postcards()

    if not rows:
        st.info("No postcards found.")
    else:
        # Convert rows into structured records
        postcards = []
        for row in rows:
            postcard_id, code, description, theme, tags, status, image_path, date_added, date_sent, notes = row
            postcards.append({
                "id": postcard_id,
                "code": code or "",
                "description": description or "",
                "theme": theme or "",
                "tags": tags or "",
                "status": status or "",
                "image_path": image_path or "",
                "date_added": date_added or "",
                "date_sent": date_sent or "",
                "notes": notes or "",
            })

        theme_options = ["all"] + sorted({p["theme"] for p in postcards if p["theme"]})

        col_filter1, col_filter2 = st.columns(2)

        with col_filter1:
            filter_status = st.selectbox(
                "Filter by status",
                ["all", "draft", "in_stock", "sent", "reserved", "given_away"]
            )

        with col_filter2:
            filter_theme = st.selectbox(
                "Filter by theme",
                theme_options
            )
        
        search_text = st.text_input(
            "Search postcards",
            placeholder="Search by code, description, theme, tags, notes, or status"
        ).strip().lower()

        # Apply filters
        filtered_postcards = []
        for postcard in postcards:
            if filter_status != "all" and postcard["status"] != filter_status:
                continue

            if filter_theme != "all" and postcard["theme"] != filter_theme:
                continue

            searchable_text = " ".join([
                postcard["code"],
                postcard["description"],
                postcard["theme"],
                postcard["tags"],
                postcard["status"],
                postcard["notes"],
            ]).lower()

            if search_text and search_text not in searchable_text:
                continue

            filtered_postcards.append(postcard)

        st.caption(f"Showing {len(filtered_postcards)} of {len(postcards)} postcards")

        # CSV export helper
        def postcards_to_csv(records):
            import csv
            import io

            output = io.StringIO()
            writer = csv.writer(output)
            writer.writerow([
                "id", "code", "description", "theme", "tags", "status",
                "image_path", "date_added", "date_sent", "notes"
            ])

            for p in records:
                writer.writerow([
                    p["id"],
                    p["code"],
                    p["description"],
                    p["theme"],
                    p["tags"],
                    p["status"],
                    p["image_path"],
                    p["date_added"],
                    p["date_sent"],
                    p["notes"],
                ])

            return output.getvalue().encode("utf-8")

        col1, col2 = st.columns(2)

        with col1:
            st.download_button(
                label="Download Filtered Results (CSV)",
                data=postcards_to_csv(filtered_postcards),
                file_name="postcards_filtered.csv",
                mime="text/csv"
            )

        with col2:
            st.download_button(
                label="Download All Postcards (CSV)",
                data=postcards_to_csv(postcards),
                file_name="postcards_all.csv",
                mime="text/csv"
            )

        if not filtered_postcards:
            st.warning("No postcards matched your search/filter.")
        else:
            st.subheader("Inventory Table")

            table_rows = []
            for p in filtered_postcards:
                table_rows.append({
                    "ID": p["id"],
                    "Code": p["code"],
                    "Theme": p["theme"],
                    "Status": p["status"],
                    "Date Added": p["date_added"],
                    "Date Sent": p["date_sent"],
                    "Description": p["description"][:100] + ("..." if len(p["description"]) > 100 else "")
                })

            st.dataframe(
                table_rows,
                use_container_width=True,
                hide_index=True
            )

            preview_options = {
                f"ID {p['id']} | {p['code'] if p['code'] else '(no code yet)'} | {p['theme']} | {p['status']}": p["id"]
                for p in filtered_postcards
            }

            selected_label = st.selectbox(
                "Select a postcard to preview full details",
                list(preview_options.keys())
            )

            selected_id = preview_options[selected_label]
            selected_postcard = next(p for p in filtered_postcards if p["id"] == selected_id)

            st.subheader("Postcard Details")

            col_left, col_right = st.columns([1, 2])

            with col_left:
                if selected_postcard["image_path"] and Path(selected_postcard["image_path"]).exists():
                    st.image(selected_postcard["image_path"], width=280)
                else:
                    st.info("No image found.")

            with col_right:
                st.write(f"**ID:** {selected_postcard['id']}")
                st.write(f"**Code:** {selected_postcard['code'] if selected_postcard['code'] else '(no code yet)'}")
                st.write(f"**Theme:** {selected_postcard['theme']}")
                st.write(f"**Status:** {selected_postcard['status']}")
                st.write(f"**Description:** {selected_postcard['description']}")
                st.write(f"**Tags:** {selected_postcard['tags']}")
                st.write(f"**Date Added:** {selected_postcard['date_added']}")
                st.write(f"**Date Sent:** {selected_postcard['date_sent']}")
                st.write(f"**Notes:** {selected_postcard['notes']}")
                st.write(f"**Image Path:** {selected_postcard['image_path']}")
                st.subheader("Edit Postcard")

                edit_description = st.text_area(
                    "Edit Description",
                    value=selected_postcard["description"],
                    key=f"edit_description_{selected_postcard['id']}",
                    height=100
                )

                edit_theme = st.selectbox(
                    "Edit Theme",
                    list(THEME_MAP.keys()),
                    index=list(THEME_MAP.keys()).index(selected_postcard["theme"]) if selected_postcard["theme"] in THEME_MAP else 0,
                    key=f"edit_theme_{selected_postcard['id']}"
                )

                edit_tags = st.text_area(
                    "Edit Tags (; separated)",
                    value=selected_postcard["tags"],
                    key=f"edit_tags_{selected_postcard['id']}",
                    height=100
                )

                edit_status = st.selectbox(
                    "Edit Status",
                    ["draft", "in_stock", "sent", "reserved", "given_away"],
                    index=["draft", "in_stock", "sent", "reserved", "given_away"].index(selected_postcard["status"]) if selected_postcard["status"] in ["draft", "in_stock", "sent", "reserved", "given_away"] else 1,
                    key=f"edit_status_{selected_postcard['id']}"
                )

                edit_notes = st.text_area(
                    "Edit Notes",
                    value=selected_postcard["notes"],
                    key=f"edit_notes_{selected_postcard['id']}",
                    height=100
                )

                col_edit1, col_edit2 = st.columns(2)

                with col_edit1:
                    if st.button(f"💾 Save Changes for ID {selected_postcard['id']}"):
                        try:
                            update_postcard_fields(
                                postcard_id=selected_postcard["id"],
                                description=edit_description,
                                theme=edit_theme,
                                tags=edit_tags,
                                status=edit_status,
                                notes=edit_notes
                            )
                            st.success("Postcard updated successfully.")
                            st.rerun()
                        except Exception as e:
                            st.error(str(e))

                with col_edit2:
                    st.caption("Code is read-only for now to avoid breaking theme-based numbering.")

            with st.expander("Show all filtered postcards as full cards"):
                for postcard in filtered_postcards:
                    st.markdown(
                        f"### ID {postcard['id']} | {postcard['code'] if postcard['code'] else '(no code yet)'}"
                    )

                    if postcard["image_path"] and Path(postcard["image_path"]).exists():
                        st.image(postcard["image_path"], width=220)

                    st.write(f"**Theme:** {postcard['theme']}")
                    st.write(f"**Description:** {postcard['description']}")
                    st.write(f"**Tags:** {postcard['tags']}")
                    st.write(f"**Status:** {postcard['status']}")
                    st.write(f"**Date Added:** {postcard['date_added']}")
                    st.write(f"**Date Sent:** {postcard['date_sent']}")
                    st.write(f"**Notes:** {postcard['notes']}")
                    st.write(f"**Image Path:** {postcard['image_path']}")
                    st.divider()