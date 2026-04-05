import re
import os
import heapq
from fastapi import FastAPI, HTTPException
from pymongo import MongoClient
from thefuzz import process

app = FastAPI(title="Jordan Transport Smart API")

# --- الإعدادات والاتصال ---
MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017/")
client = MongoClient(MONGO_URI)
db = client["JordanTransport"]
collection = db["FullNetwork"]

# --- 1. وظائف المساعدة (Helpers) ---

def normalize_arabic(text):
    if not text: return ""
    text = str(text).strip()
    text = re.sub("[إأآ]", "ا", text)
    text = re.sub("ة", "ه", text)
    arabic_diacritics = re.compile(""" ّ | َ | ً | ُ | ٌ | ِ | ٍ | ْ | ـ """, re.VERBOSE)
    return re.sub(arabic_diacritics, '', text)

def get_all_places_list():
    """وظيفة موحدة لجلب قائمة بكل المناطق من الداتابيز"""
    sources = collection.distinct("source")
    all_docs = collection.find({}, {"destinations": 1})
    dest_keys = []
    for d in all_docs:
        if "destinations" in d:
            dest_keys.extend(d["destinations"].keys())
    # تنظيف، حذف الفراغات، حذف التكرار، والترتيب
    return sorted(list(set([p.strip() for p in (sources + dest_keys) if p])))

def get_suggestions(user_input, all_places, limit=3):
    normalized_input = normalize_arabic(user_input)
    matches = process.extract(normalized_input, all_places, limit=limit)
    return [m[0] for m in matches if m[1] >= 50]

# --- 2. خوارزمية دايكسترا ---

def get_neighbors_from_db(node_name):
    neighbors = {}
    node_name = node_name.strip()
    
    doc_src = collection.find_one({"source": node_name})
    if doc_src and "destinations" in doc_src:
        for neighbor, edges in doc_src["destinations"].items():
            neighbors.setdefault(neighbor.strip(), []).extend(edges)

    docs_dest = collection.find({"destinations." + node_name: {"$exists": True}})
    for doc in docs_dest:
        source_name = doc["source"].strip()
        for neighbor, edges in doc["destinations"].items():
            for edge in edges:
                if edge.get("destination") == node_name or neighbor == node_name:
                    neighbors.setdefault(source_name, []).append({
                        "cost": edge["cost"], "line": edge["line"]
                    })
    return neighbors

def dijkstra_mongodb(start_node, end_node):
    distances = {start_node: 0} 
    visited_info = {}
    priority_queue = [(0, start_node)]

    while priority_queue:
        current_distance, current_node = heapq.heappop(priority_queue)
        if current_node == end_node: break
        if current_distance > distances.get(current_node, float('inf')): continue

        neighbors = get_neighbors_from_db(current_node)
        for neighbor, edges in neighbors.items():
            for edge in edges:
                new_distance = current_distance + edge["cost"]
                if new_distance < distances.get(neighbor, float('inf')):
                    distances[neighbor] = new_distance
                    visited_info[neighbor] = (current_node, edge["line"])
                    heapq.heappush(priority_queue, (new_distance, neighbor))

    if end_node not in distances: return None, float('inf'), []

    path, lines, current = [], [], end_node
    while current != start_node:
        prev_node, line_used = visited_info[current]
        path.append(current); lines.append(line_used)
        current = prev_node
        
    path.append(start_node)
    path.reverse(); lines.reverse()

    clean_lines = [lines[0]] if lines else []
    for i in range(1, len(lines)):
        if lines[i] != lines[i-1]: clean_lines.append(lines[i])

    return path, distances[end_node], clean_lines

# --- 3. المسارات (Endpoints) ---

@app.get("/all-places")
async def all_places_endpoint():
    """يعيد قائمة بكل المناطق المتاحة لزميلك في الـ UI"""
    places = get_all_places_list()
    return {"status": "success", "count": len(places), "places": places}

@app.get("/suggest")
async def suggest_endpoint(q: str):
    """يعيد مقترحات أثناء الكتابة"""
    places = get_all_places_list()
    suggestions = get_suggestions(q, places, limit=5)
    return {"status": "success", "suggestions": suggestions}

@app.get("/get-route")
async def get_route(start: str, end: str):
    all_places = get_all_places_list()

    # محاولة التطابق بدقة عالية (90%)
    match_start = process.extractOne(normalize_arabic(start), all_places, score_cutoff=90)
    match_end = process.extractOne(normalize_arabic(end), all_places, score_cutoff=90)

    if not match_start or not match_end:
        return {
            "status": "ambiguous",
            "message": "الرجاء اختيار المنطقة الصحيحة من المقترحات",
            "suggestions": {
                "start": get_suggestions(start, all_places) if not match_start else [match_start[0]],
                "end": get_suggestions(end, all_places) if not match_end else [match_end[0]]
            }
        }

    corrected_start, corrected_end = match_start[0], match_end[0]
    path, cost, lines = dijkstra_mongodb(corrected_start, corrected_end)

    if not path:
        return {"status": "no_path", "corrected_names": {"from": corrected_start, "to": corrected_end}}

    return {
        "status": "success",
        "corrected_names": {"from": corrected_start, "to": corrected_end},
        "path": path, "total_cost": round(cost, 3), "lines": lines
    }
