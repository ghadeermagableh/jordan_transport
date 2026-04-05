import re
import os
import heapq
from fastapi import FastAPI, HTTPException
from pymongo import MongoClient
from thefuzz import process

app = FastAPI(title="Jordan Transport Smart API")

# إعداد الاتصال باستخدام متغير البيئة (للرفع) أو المحلي (للتجربة)
MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017/")
client = MongoClient(MONGO_URI)
db = client["JordanTransport"]
collection = db["FullNetwork"]

# --- 1. وظائف المعالجة والبحث الذكي (Fuzzy Matching) ---

def normalize_arabic(text):
    if not text: return ""
    text = re.sub("[إأآ]", "ا", text)
    text = re.sub("ة", "ه", text)
    arabic_diacritics = re.compile(""" ّ | َ | ً | ُ | ٌ | ِ | ٍ | ْ | ـ """, re.VERBOSE)
    return re.sub(arabic_diacritics, '', text).strip()

def get_best_match(user_input, all_places):
    normalized_input = normalize_arabic(user_input)
    # تطابق تام
    for place in all_places:
        if normalize_arabic(place) == normalized_input:
            return place
    
    # تطابق مرن
    matches = process.extractOne(normalized_input, all_places, score_cutoff=60)
    return matches[0] if matches else None

# --- 2. خوارزمية دايكسترا (النسخة المعتمدة لديكِ) ---

def get_neighbors_from_db(node_name):
    neighbors = {}
    # 1- المسارات من هذا المصدر
    doc_src = collection.find_one({"source": node_name})
    if doc_src and "destinations" in doc_src:
        for neighbor, edges in doc_src["destinations"].items():
            neighbors.setdefault(neighbor, []).extend(edges)

    # 2- المسارات حيث node_name موجود كوجهة (دعم الاتجاهين)
    docs_dest = collection.find({"destinations." + node_name: {"$exists": True}})
    for doc in docs_dest:
        for neighbor, edges in doc["destinations"].items():
            for edge in edges:
                if edge.get("destination") == node_name or neighbor == node_name:
                    neighbors.setdefault(doc["source"], []).append({
                        "cost": edge["cost"],
                        "line": edge["line"]
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

    if end_node not in distances:
        return None, float('inf'), []

    path, lines, current = [], [], end_node
    while current != start_node:
        prev_node, line_used = visited_info[current]
        path.append(current)
        lines.append(line_used)
        current = prev_node
        
    path.append(start_node)
    path.reverse()
    lines.reverse()

    # تنظيف الخطوط (Clean Lines)
    clean_lines = []
    if lines:
        clean_lines.append(lines[0]) 
        for i in range(1, len(lines)):
            if lines[i] != lines[i-1]:
                clean_lines.append(lines[i])

    return path, distances[end_node], clean_lines

# --- 3. المسارات (Endpoints) ---

@app.get("/get-route")
async def get_route(start: str, end: str):
    # جلب قائمة بكل المناطق للبحث الذكي
# جلب أسماء المناطق من الـ source ومن مفاتيح الـ destinations بشكل صحيح
    sources = collection.distinct("source")
    
    # جلب كل المفاتيح الموجودة داخل حقول destinations
    all_docs = collection.find({}, {"destinations": 1})
    dest_keys = []
    for doc in all_docs:
        if "destinations" in doc:
            dest_keys.extend(doc["destinations"].keys())
    
    # دمج القائمتين وحذف التكرار
    all_places = list(set(sources + dest_keys))    
    corrected_start = get_best_match(start, all_places)
    corrected_end = get_best_match(end, all_places)

    if not corrected_start or not corrected_end:
        raise HTTPException(status_code=404, detail="اسم المنطقة غير واضح، حاول كتابة الاسم بشكل أدق")

    path, cost, lines = dijkstra_mongodb(corrected_start, corrected_end)

    if not path:
        return {
            "status": "no_path", 
            "corrected_names": {"from": corrected_start, "to": corrected_end},
            "message": "لا يوجد مسار مسجل بين هذه المناطق"
        }

    return {
        "status": "success",
        "corrected_names": {"from": corrected_start, "to": corrected_end},
        "path": path,
        "total_cost": round(cost, 3),
        "lines": lines
    }
