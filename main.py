import re
import os
import heapq
from fastapi import FastAPI, HTTPException
from pymongo import MongoClient
from thefuzz import process

app = FastAPI(title="Jordan Transport Smart API")

# إعداد الاتصال (استخدام متغير البيئة للرفع)
MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017/")
client = MongoClient(MONGO_URI)
db = client["JordanTransport"]
collection = db["FullNetwork"]

# --- 1. وظائف المعالجة والبحث الذكي ---

def normalize_arabic(text):
    if not text: return ""
    text = re.sub("[إأآ]", "ا", text)
    text = re.sub("ة", "ه", text)
    arabic_diacritics = re.compile(""" ّ | َ | ً | ُ | ٌ | ِ | ٍ | ْ | ـ """, re.VERBOSE)
    return re.sub(arabic_diacritics, '', text).strip()

def get_best_match(user_input, all_places):
    """تصحيح اسم المنطقة تلقائياً"""
    normalized_input = normalize_arabic(user_input)
    # البحث عن تطابق تام أولاً
    for place in all_places:
        if normalize_arabic(place) == normalized_input:
            return place
    
    # البحث المرن إذا لم يوجد تطابق تام
    matches = process.extractOne(normalized_input, all_places, score_cutoff=60)
    return matches[0] if matches else None

# --- 2. خوارزمية دايكسترا ---

def get_neighbors(node_name):
    neighbors = {}
    doc = collection.find_one({"source": node_name})
    if doc and "destinations" in doc:
        for neighbor, edges in doc["destinations"].items():
            neighbors.setdefault(neighbor, []).extend(edges)
    return neighbors

def run_dijkstra(start, end):
    distances = {start: 0}
    visited_info = {}
    pq = [(0, start)]

    while pq:
        curr_dist, curr_node = heapq.heappop(pq)
        if curr_node == end: break
        if curr_dist > distances.get(curr_node, float('inf')): continue

        for neighbor, edges in get_neighbors(curr_node).items():
            for edge in edges:
                new_dist = curr_dist + edge["cost"]
                if new_dist < distances.get(neighbor, float('inf')):
                    distances[neighbor] = new_dist
                    visited_info[neighbor] = (curr_node, edge["line"])
                    heapq.heappush(pq, (new_dist, neighbor))

    if end not in distances: return None, None, None
    
    path, lines, curr = [], [], end
    while curr != start:
        prev, line = visited_info[curr]
        path.append(curr); lines.append(line)
        curr = prev
    path.append(start)
    
    # تنظيف الخطوط (Clean Lines)
    lines.reverse()
    clean_lines = [lines[0]] if lines else []
    for i in range(1, len(lines)):
        if lines[i] != lines[i-1]: clean_lines.append(lines[i])
        
    return path[::-1], distances[end], clean_lines

# --- 3. الـ Endpoints (المسارات) ---

@app.get("/get-route")
async def get_route(start: str, end: str):
    all_places = collection.distinct("source")
    
    # تصحيح الأسماء تلقائياً قبل البدء بالخوارزمية
    corrected_start = get_best_match(start, all_places)
    corrected_end = get_best_match(end, all_places)

    if not corrected_start or not corrected_end:
        raise HTTPException(status_code=404, detail="لم يتم العثور على مناطق قريبة للأسماء المدخلة")

    path, cost, lines = run_dijkstra(corrected_start, corrected_end)

    if not path:
        return {"status": "no_path", "message": f"لا يوجد مسار بين {corrected_start} و {corrected_end}"}

    return {
        "status": "success",
        "original_input": {"from": start, "to": end},
        "corrected_names": {"from": corrected_start, "to": corrected_end},
        "path": path,
        "total_cost": round(cost, 3),
        "lines": lines
    }