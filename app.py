"""
PDF to CBT Converter — Flask Backend
Features: 4-key rotation, 5s delay, diagram extraction via PyMuPDF, integer questions
"""
import os, json, uuid, re, time, traceback, threading
from datetime import datetime
from flask import Flask, render_template, request, jsonify, redirect, url_for, session, send_from_directory
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from google import genai
from google.genai import types
from pydantic import BaseModel
from typing import List, Dict, Optional, Any
import fitz  # PyMuPDF
import database

# ======================================================================
# API KEY ROTATION — Round-robin with auto-failover
# ======================================================================
API_KEYS = [k for k in [
    os.environ.get("GEMINI_KEY_1"),
    os.environ.get("GEMINI_KEY_2"),
    os.environ.get("GEMINI_KEY_3"),
    os.environ.get("GEMINI_KEY_4"),
] if k]

if not API_KEYS:
    # We no longer hardcode keys here because they get disabled by Google if pushed to GitHub.
    # Users must set GEMINI_KEY_1, GEMINI_KEY_2, etc. in their environment.
    print("[Boot] ERROR: No API keys found in environment variables!")
    print("[Boot] Please set GEMINI_KEY_1, GEMINI_KEY_2, etc. before running.")
    # We leave API_KEYS empty so the app fails gracefully with a clear message rather than a 403.
    API_KEYS = []

_key_index = 0
_key_lock = threading.Lock()
_last_request_time = 0
REQUEST_DELAY = 5  # seconds between Gemini requests


def _get_client():
    """Get a Gemini client with the current key (round-robin)."""
    global _key_index
    if not API_KEYS:
        raise Exception("No Gemini API keys configured. Please set GEMINI_KEY_1 in environment.")
    with _key_lock:
        key = API_KEYS[_key_index]
        _key_index = (_key_index + 1) % len(API_KEYS)
    print(f"[KeyRotation] Using key ...{key[-8:]}")
    return genai.Client(api_key=key)


_rate_lock = threading.Lock()
def _rate_limit_wait():
    """Enforce minimum delay between Gemini requests."""
    global _last_request_time
    wait = 0
    with _rate_lock:
        now = time.time()
        elapsed = now - _last_request_time
        if elapsed < REQUEST_DELAY:
            wait = REQUEST_DELAY - elapsed
            
    if wait > 0:
        print(f"[RateLimit] Waiting {wait:.1f}s before next request")
        time.sleep(wait)
        
    with _rate_lock:
        _last_request_time = time.time()


class QuestionSchema(BaseModel):
    id: str
    subject: str
    topic: str
    text: str
    type: str
    options: Optional[Dict[str, str]] = None
    correct_answer: str
    page_number: int
    has_diagram: bool
    diagram_bbox: Optional[List[float]] = None

class TestSchema(BaseModel):
    test_name: Optional[str] = "Unnamed Test"
    subjects: Optional[List[str]] = None
    questions: List[QuestionSchema]
    duration_minutes: Optional[int] = 180

class SubjectAnalysis(BaseModel):
    score_comment: str
    weak_topics: List[str]
    strong_topics: List[str]
    key_gaps: str

class PriorityTopic(BaseModel):
    topic: str
    subject: str
    reason: str
    study_plan: str

class AnalysisSchema(BaseModel):
    summary: str
    subject_analysis: Dict[str, SubjectAnalysis]
    recommendations: List[str]
    priority_topics: List[PriorityTopic]
    common_mistakes: List[str]

def _call_gemini(contents, max_retries=8, client=None, response_schema=None):
    """Call Gemini with key rotation + retry on 429."""
    keys_tried = 0
    config = types.GenerateContentConfig()
    if response_schema:
        config.response_schema = response_schema
        config.response_mime_type = "application/json"

    for attempt in range(max_retries):
        _rate_limit_wait()
        current_client = client if client else _get_client()
        try:
            response = current_client.models.generate_content(
                model="gemini-2.5-flash",
                contents=contents,
                config=config
            )
            return response.text
        except Exception as e:
            err = str(e)
            if any(x in err for x in ['429', 'RESOURCE_EXHAUSTED', '503', 'UNAVAILABLE']):
                if client:
                    print(f"[Gemini] Error on fixed client: {err[:80]}... triggering full rotation")
                    raise Exception(f"FIXED_CLIENT_EXHAUSTED: {err}")
                keys_tried += 1
                print(f"[Gemini] Error caught (attempt {attempt+1}): {err[:80]}... rotating key")
                if keys_tried >= len(API_KEYS) * 2:
                    raise Exception(
                        "All 4 API keys exhausted or unavailable. Please wait a few minutes for quota reset, "
                        "or try again later."
                    )
                time.sleep(5)  # Wait a bit longer for 503s
                continue
            raise
    raise Exception("Max retries exceeded for Gemini API call")



# ======================================================================
# FLASK APP
# ======================================================================
app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024

from werkzeug.exceptions import HTTPException

@app.errorhandler(Exception)
def handle_exception(e):
    # Pass through HTTP errors as JSON instead of HTML
    if isinstance(e, HTTPException):
        return jsonify(error=e.description), e.code
    # Handle non-HTTP exceptions
    return jsonify(error=str(e)), 500

# Initialize Database
database.init_db()

UPLOAD_DIR = os.path.join(os.path.dirname(__file__), 'uploads')
os.makedirs(UPLOAD_DIR, exist_ok=True)

# Use persistent storage for images
IMAGE_DIR = os.path.join(database.DATA_DIR, 'test_images')
os.makedirs(IMAGE_DIR, exist_ok=True)

# Define route to serve images from persistent storage
@app.route('/test_images/<test_id>/<filename>')
def serve_test_image(test_id, filename):
    return send_from_directory(os.path.join(IMAGE_DIR, test_id), filename)

# ======================================================================
# PDF IMAGE EXTRACTION — Extract individual diagrams from PDF
# ======================================================================
def extract_images_from_pdf(pdf_path, test_id):
    """Extract individual embedded images from PDF."""
    img_dir = os.path.join(IMAGE_DIR, test_id)
    os.makedirs(img_dir, exist_ok=True)

    doc = fitz.open(pdf_path)
    extracted = []
    global_idx = 0
    max_pages = min(len(doc), 120)

    for page_num in range(max_pages):
        page = doc[page_num]
        image_list = page.get_images(full=True)

        for img_info in image_list:
            xref = img_info[0]
            try:
                base_image = doc.extract_image(xref)
                img_bytes = base_image["image"]
                ext = base_image.get("ext", "png")
                # Skip tiny images (icons, bullets, etc) — less than 2KB
                if len(img_bytes) < 2048:
                    continue
                filename = f"img_{global_idx}.{ext}"
                filepath = os.path.join(img_dir, filename)
                with open(filepath, 'wb') as f:
                    f.write(img_bytes)
                extracted.append({
                    "index": global_idx,
                    "page": page_num + 1,
                    "filename": filename,
                })
                global_idx += 1
            except Exception:
                continue

    doc.close()
    print(f"[Images] Extracted {len(extracted)} individual images from PDF")
    return extracted


# ======================================================================
# GEMINI: Extract questions from PDFs
# ======================================================================
# ======================================================================
def extract_questions_from_pdfs(q_path, a_path, test_id, image_info, max_full_retries=3):
    """Upload PDFs to Gemini and extract structured questions in chunks to avoid token limits."""
    import fitz
    doc = fitz.open(q_path)
    total_pages = min(len(doc), 120)  # Hard limit to prevent RAM DOS on huge modules
    doc.close()

    all_questions = []
    test_metadata = {}
    
    upload_client = _get_client()
    q_file, a_file = None, None
    try:
        q_file = upload_client.files.upload(file=q_path)
        a_file = upload_client.files.upload(file=a_path)

        for f in [q_file, a_file]:
            while f.state.name == "PROCESSING":
                time.sleep(2)
                f = upload_client.files.get(name=f.name)
            if f.state.name == "FAILED":
                raise Exception("Gemini failed to process file")

        img_summary = f"I extracted {len(image_info)} images from the question paper PDF."
        
        # Process in chunks of 3 pages to ensure high detail and no skipped questions
        chunk_size = 3
        for start_pg in range(1, total_pages + 1, chunk_size):
            end_pg = min(start_pg + chunk_size - 1, total_pages)
            print(f"[Process] Analyzing pages {start_pg} to {end_pg}...")
            
            for attempt in range(max_full_retries):
                prompt = f"""You are a JEE/NEET exam paper analyzer. I'm giving you two PDFs.
FOCUS ONLY ON QUESTIONS LOCATED ON PAGES {start_pg} TO {end_pg} of the Question Paper.

Extract ALL questions from these specific pages and match them with answers from the Answer Key.

CRITICAL RULES:
1. ONLY extract questions that physically start on pages {start_pg} through {end_pg}.
2. "has_diagram": Set to true for ANY question that has a figure, graph, circuit, or drawing nearby. 
3. "diagram_bbox": If has_diagram is true, provide the [ymin, xmin, ymax, xmax] coordinates (0-1000) of the figure on that page.
4. If a question is an INTEGER type, set options to null.
5. Match the question number accurately with the answer key."""

                try:
                    text = _call_gemini([prompt, q_file, a_file], client=upload_client, response_schema=TestSchema)
                    parsed = TestSchema.model_validate_json(text)
                    data = parsed.model_dump()
                    
                    # Sanity check validation
                    valid_questions = []
                    for q in data.get('questions', []):
                        if q.get('type') == 'MCQ_SINGLE':
                            opts = q.get('options')
                            if not opts or len(opts) != 4:
                                continue  # Reject malformed MCQ
                        valid_questions.append(q)
                    
                    if valid_questions:
                        all_questions.extend(valid_questions)
                    if not test_metadata and 'test_name' in data:
                        test_metadata = {
                            'test_name': data.get('test_name', 'Unnamed Test'),
                            'subjects': data.get('subjects', []),
                            'duration_minutes': data.get('duration_minutes', 180)
                        }
                    break # Success, move to next chunk

                except Exception as e:
                    err = str(e)
                    if "FIXED_CLIENT_EXHAUSTED" in err:
                        print(f"[Process] Key exhausted, re-uploading on new key and retrying chunk...")
                        try:
                            if q_file: upload_client.files.delete(name=q_file.name)
                            if a_file: upload_client.files.delete(name=a_file.name)
                        except: pass
                        upload_client = _get_client()
                        q_file = upload_client.files.upload(file=q_path)
                        a_file = upload_client.files.upload(file=a_path)
                        for f in [q_file, a_file]:
                            while f.state.name == "PROCESSING":
                                time.sleep(2)
                                f = upload_client.files.get(name=f.name)
                        continue
                    print(f"[Process] Chunk Error: {err}")
                    if attempt == max_full_retries - 1:
                        print(f"[Process] Skipping chunk {start_pg}-{end_pg} after max retries.")
    finally:
        try:
            if q_file: upload_client.files.delete(name=q_file.name)
            if a_file: upload_client.files.delete(name=a_file.name)
        except Exception as e:
            print(f"[Warn] Failed to delete Gemini files: {e}")

    # Deduplicate questions by ID
    unique_qs = {}
    for q in all_questions:
        qid = q.get('id')
        if qid not in unique_qs:
            unique_qs[qid] = q
    all_questions = list(unique_qs.values())

    # Combine all chunks
    final_data = test_metadata or {"test_name": "Extracted Test", "subjects": [], "duration_minutes": 180}
    final_data['questions'] = sorted(all_questions, key=lambda x: int(x.get('id', 0)))
    final_data['total_questions'] = len(all_questions)
    return final_data


def analyze_concepts(wrong_questions, all_questions):
    """Analyze wrong/unattempted questions and suggest topics to study."""
    wrong_details = []
    for w in wrong_questions:
        q = next((q for q in all_questions if str(q['id']) == str(w['id'])), None)
        if q:
            wrong_details.append({
                "subject": q['subject'], "topic": q.get('topic', 'General'),
                "question": q['text'][:200], "correct_answer": q['correct_answer'],
                "user_answer": w.get('user_answer', 'Not Attempted'), "type": q['type']
            })
    if not wrong_details:
        return {"summary": "Perfect score!", "subject_analysis": {},
                "recommendations": ["Keep it up!"], "priority_topics": [], "common_mistakes": []}

    prompt = f"""You are a JEE/NEET mentor. Analyze these wrong/unattempted answers:
{json.dumps(wrong_details, indent=2)}

Be specific, encouraging, and actionable."""
    try:
        text = _call_gemini(prompt, response_schema=AnalysisSchema)
        parsed = AnalysisSchema.model_validate_json(text)
        return parsed.model_dump()
    except Exception as e:
        print(f"[Analysis] Error: {e}")
        return {"summary": "Analysis failed.", "subject_analysis": {}, "recommendations": [], "priority_topics": [], "common_mistakes": []}


def _check_answer(user_ans, correct_ans, q_type):
    user_ans = str(user_ans).strip().upper()
    correct_ans = str(correct_ans).strip().upper()
    if q_type == 'MCQ_SINGLE':
        return user_ans == correct_ans
    elif q_type == 'MCQ_MULTI':
        return set(x.strip() for x in user_ans.split(',')) == set(x.strip() for x in correct_ans.split(','))
    elif q_type == 'INTEGER':
        try:
            return abs(float(user_ans) - float(correct_ans)) < 0.01
        except:
            return user_ans == correct_ans
    return user_ans == correct_ans


# ======================================================================
# FLASK-LOGIN SETUP
# ======================================================================
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login_page'
app.secret_key = os.environ.get('SECRET_KEY', os.urandom(24))

class User(UserMixin):
    def __init__(self, user_dict):
        self.id = user_dict['id']
        self.email = user_dict['email']
        self.name = user_dict.get('name', '')

@login_manager.user_loader
def load_user(user_id):
    u = database.get_user_by_id(int(user_id))
    if u:
        return User(u)
    return None


# ======================================================================
# AUTH ROUTES
# ======================================================================
@app.route('/login')
def login_page():
    if current_user.is_authenticated:
        return redirect(url_for('index'))
    return render_template('auth.html', mode='login')

@app.route('/register')
def register_page():
    if current_user.is_authenticated:
        return redirect(url_for('index'))
    return render_template('auth.html', mode='register')

@app.route('/api/register', methods=['POST'])
def api_register():
    data = request.get_json(silent=True) or {}
    name = (data.get('name') or '').strip()
    email = (data.get('email') or '').strip()
    password = data.get('password', '')
    if not email or not password or not name:
        return jsonify({'error': 'Name, email and password are required'}), 400
    if len(password) < 6:
        return jsonify({'error': 'Password must be at least 6 characters'}), 400
    pw_hash = generate_password_hash(password)
    user_id = database.create_user(email, pw_hash, name)
    if user_id is None:
        return jsonify({'error': 'An account with this email already exists'}), 409
    user_dict = database.get_user_by_id(user_id)
    login_user(User(user_dict), remember=True)
    return jsonify({'success': True, 'redirect': '/'})

@app.route('/api/login', methods=['POST'])
def api_login():
    data = request.get_json(silent=True) or {}
    email = (data.get('email') or '').strip()
    password = data.get('password', '')
    user_dict = database.get_user_by_email(email)
    if not user_dict or not check_password_hash(user_dict['password_hash'], password):
        return jsonify({'error': 'Incorrect email or password'}), 401
    login_user(User(user_dict), remember=True)
    return jsonify({'success': True, 'redirect': '/'})

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login_page'))


# ======================================================================
# ROUTES
# ======================================================================
@app.route('/')
@login_required
def index():
    return render_template('index.html')


def _background_process_pdfs(q_path, a_path, test_id, user_id):
    try:
        # Step 1: Extract individual images from question paper PDF
        print(f"[Process] Extracting images for {test_id}...")
        image_info = extract_images_from_pdf(q_path, test_id)

        # Step 2: Send to Gemini for question extraction with image mapping
        print(f"[Process] Sending to Gemini for extraction...")
        test_data = extract_questions_from_pdfs(q_path, a_path, test_id, image_info)
        test_data['test_id'] = test_id
        test_data['image_info'] = image_info
        test_data['created_at'] = datetime.now().isoformat()

        # Crop diagrams using bbox coordinates from Gemini (works for both vector AND raster)
        try:
            from PIL import Image as PILImage, ImageStat

            def _trim_leading_text(img):
                """Scan from the top and trim text lines that leaked above the diagram."""
                gray = img.convert('L')
                width, height = gray.size
                if width == 0 or height == 0:
                    return img

                pixels = list(gray.getdata())
                saw_content = False
                new_top = 0

                for y in range(height):
                    row = pixels[y * width:(y + 1) * width]
                    dark_count = sum(1 for p in row if p < 210)
                    if (dark_count / width) > 0.01:
                        saw_content = True
                    elif saw_content:
                        new_top = y
                        break

                if new_top > 4:
                    return img.crop((0, new_top, width, height))
                return img

            img_dir = os.path.join(IMAGE_DIR, test_id)
            doc_for_rendering = None
            
            for q in test_data.get('questions', []):
                if q.get('has_diagram') and q.get('diagram_bbox') and q.get('page_number'):
                    bbox = q['diagram_bbox']
                    pg = q['page_number']
                    if isinstance(bbox, list) and len(bbox) == 4:
                        page_path = os.path.join(img_dir, f"page_{pg}.png")
                        
                        # Render page on demand to save RAM/Time
                        if not os.path.exists(page_path):
                            if doc_for_rendering is None:
                                doc_for_rendering = fitz.open(q_path)
                            if 0 <= pg - 1 < len(doc_for_rendering):
                                page = doc_for_rendering[pg - 1]
                                pix = page.get_pixmap(dpi=250)
                                pix.save(page_path)
                                
                        if os.path.exists(page_path):
                            img = PILImage.open(page_path)
                            W, H = img.size
                            ymin, xmin, ymax, xmax = [float(c) for c in bbox]
                            left   = max(0, (xmin / 1000) * W - 20)
                            top    = max(0, (ymin / 1000) * H - 20)
                            right  = min(W, (xmax / 1000) * W + 20)
                            bottom = min(H, (ymax / 1000) * H + 20)
                            if right > left and bottom > top:
                                cropped = img.crop((left, top, right, bottom))
                                # Auto-trim any text that leaked in from above the figure
                                cropped = _trim_leading_text(cropped)
                                fname = f"q{q['id']}_diagram.png"
                                cropped.save(os.path.join(img_dir, fname))
                                q['diagram_crop'] = fname
                                print(f"[Diagram] Cropped diagram for Q{q['id']} -> {fname}")
            if doc_for_rendering:
                doc_for_rendering.close()
        except Exception as e:
            print(f"[Diagram] Crop error: {e}")

        qcount = len(test_data.get('questions', []))
        diagram_questions = [q for q in test_data.get('questions', []) if q.get('has_diagram')]
        
        # Save as draft — needs diagram review before going live
        test_data['status'] = 'draft'
        test_data['needs_review'] = len(diagram_questions) > 0
        database.save_test(test_id, test_data.get('test_name', 'Test'), test_data, user_id)

        print(f"[Process] ✅ Extracted {qcount} questions, {len(diagram_questions)} diagram questions")
        
    except Exception as e:
        print(f"[Process] ERROR: {traceback.format_exc()}")
        img_dir = os.path.join(IMAGE_DIR, test_id)
        import shutil
        shutil.rmtree(img_dir, ignore_errors=True)
        error_msg = str(e)
        if 'quota' in error_msg.lower() or '429' in error_msg or 'exhausted' in error_msg.lower():
            error_msg = ("⚠️ All API keys exhausted! Please wait a few minutes for quota reset.")
        else:
            error_msg = ("⚠️ Internal server error during processing. Please try again later.")
        database.save_test(test_id, "Error", {"status": "error", "error": error_msg}, user_id)
    finally:
        for p in [q_path, a_path]:
            try: os.remove(p)
            except: pass

@app.route('/api/test/<test_id>/duration', methods=['PATCH'])
@login_required
def update_duration(test_id):
    data = request.get_json(silent=True) or {}
    new_duration = int(data.get('duration_minutes', 180))
    
    test = database.get_test(test_id, current_user.id)
    if not test:
        return jsonify({'error': 'Not found'}), 404
        
    test['duration_minutes'] = new_duration
    database.save_test(test_id, test.get('test_name', 'Test'), test, current_user.id)
    return jsonify({'success': True, 'duration_minutes': new_duration})

@app.route('/api/process_status/<test_id>')
@login_required
def process_status(test_id):
    test_data = database.get_test(test_id, current_user.id)
    if not test_data:
        return jsonify({"error": "Not found"}), 404
    return jsonify({
        "status": test_data.get('status', 'ready'),
        "error": test_data.get('error'),
        "needs_review": test_data.get('needs_review', False)
    })

@app.route('/api/process', methods=['POST'])
@login_required
def process_pdfs():
    if 'question_paper' not in request.files or 'answer_key' not in request.files:
        return jsonify({"error": "Both PDFs required"}), 400
    q_file = request.files['question_paper']
    a_file = request.files['answer_key']
    if not q_file.filename.endswith('.pdf') or not a_file.filename.endswith('.pdf'):
        return jsonify({"error": "Only PDF files accepted"}), 400

    test_id = str(uuid.uuid4())[:8]
    q_path = os.path.join(UPLOAD_DIR, f"{test_id}_q.pdf")
    a_path = os.path.join(UPLOAD_DIR, f"{test_id}_a.pdf")
    q_file.save(q_path)
    a_file.save(a_path)

    valid = True
    with open(q_path, 'rb') as f:
        if f.read(4) != b'%PDF': valid = False
    with open(a_path, 'rb') as f:
        if f.read(4) != b'%PDF': valid = False

    if not valid:
        try:
            os.remove(q_path)
            os.remove(a_path)
        except Exception: pass
        return jsonify({"error": "One or both files are not valid PDFs (corrupted or renamed)"}), 400

    # Mark as processing in DB immediately
    database.save_test(test_id, "Processing...", {"status": "processing", "progress": 0}, current_user.id)
    
    # Start background processing thread
    t = threading.Thread(target=_background_process_pdfs, args=(q_path, a_path, test_id, current_user.id))
    t.start()

    return jsonify({
        "success": True,
        "test_id": test_id,
        "status": "processing"
    })


# ======================================================================
# DIAGRAM REVIEW: Human-in-the-loop crop correction
# ======================================================================

@app.route('/review/<test_id>')
@login_required
def review_page(test_id):
    test = database.get_test(test_id, current_user.id)
    if not test:
        return redirect(url_for('index'))
    return render_template('review.html', test_id=test_id)


@app.route('/api/review/<test_id>')
@login_required
def get_review_data(test_id):
    test = database.get_test(test_id, current_user.id)
    if not test:
        return jsonify({'error': 'Not found'}), 404
    all_qs = []
    for q in test.get('questions', []):
        all_qs.append({
            'id': q['id'],
            'subject': q.get('subject', ''),
            'type': q.get('type', 'MCQ_SINGLE'),
            'text': q.get('text', '')[:150] + ('...' if len(q.get('text', '')) > 150 else ''),
            'page_number': q.get('page_number'),
            'has_diagram': q.get('has_diagram', False),
            'diagram_crop': q.get('diagram_crop'),
            'page_image': f"page_{q.get('page_number')}.png"
        })
    return jsonify({
        'test_name': test.get('test_name', 'Test'),
        'test_id': test_id,
        'total_questions': len(all_qs),
        'questions': all_qs
    })


@app.route('/api/crop/<test_id>/<int:q_id>', methods=['POST', 'DELETE'])
@login_required
def manage_crop(test_id, q_id):
    """Handle POST (crop) and DELETE (remove) for a specific question diagram."""
    if not re.match(r'^[a-zA-Z0-9_-]{4,16}$', test_id):
        return jsonify({'error': 'Invalid test ID'}), 400

    test = database.get_test(test_id, current_user.id)
    if not test: return jsonify({'error': 'Not found'}), 404

    q = next((q for q in test.get('questions', []) if str(q['id']) == str(q_id)), None)
    if not q: return jsonify({'error': 'Question not found'}), 404

    img_dir = os.path.join(IMAGE_DIR, test_id)
    fname = f'q{q_id}_diagram.png'
    fpath = os.path.join(img_dir, fname)

    if request.method == 'DELETE':
        try:
            if os.path.exists(fpath): os.remove(fpath)
        except Exception as e:
            print(f"[Warn] Failed to remove crop image: {e}")
        q['diagram_crop'] = None
        q['has_diagram'] = False
        database.save_test(test_id, test.get('test_name', 'Test'), test, current_user.id)
        return jsonify({'success': True})

    data = request.get_json(silent=True) or {}
    if 'x1' not in data: return jsonify({'error': 'Missing coords'}), 400
    x1, y1, x2, y2 = int(data['x1']), int(data['y1']), int(data['x2']), int(data['y2'])
    page_num = data['page_number']

    page_path = os.path.join(img_dir, f'page_{page_num}.png')
    if not os.path.exists(page_path):
        return jsonify({'error': 'Page image not found'}), 404

    from PIL import Image as PILImage
    try:
        img = PILImage.open(page_path)
        W, H = img.size
        left   = max(0, min(x1, x2))
        top    = max(0, min(y1, y2))
        right  = min(W, max(x1, x2))
        bottom = min(H, max(y1, y2))
        if right <= left or bottom <= top:
            return jsonify({'error': 'Invalid crop region'}), 400
        cropped = img.crop((left, top, right, bottom))
        cropped.save(fpath)
        q['diagram_crop'] = fname
        q['has_diagram'] = True
        database.save_test(test_id, test.get('test_name', 'Test'), test, current_user.id)
        return jsonify({'success': True, 'diagram_crop': fname, 'cache_bust': str(uuid.uuid4())[:8]})
    except Exception as e:
        print(f"[Warn] Crop error: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/question/<test_id>/<int:q_id>', methods=['PUT'])
@login_required
def update_question(test_id, q_id):
    """Manually correct OCR mistakes or malformed questions."""
    test_data = database.get_test(test_id, current_user.id)
    if not test_data: return jsonify({'error': 'Not found'}), 404

    q = next((q for q in test_data.get('questions', []) if str(q['id']) == str(q_id)), None)
    if not q: return jsonify({'error': 'Question not found'}), 404

    data = request.get_json(silent=True) or {}
    q['text'] = data.get('text', q['text'])
    q['correct_answer'] = data.get('correct_answer', q['correct_answer'])
    
    if q['type'] == 'MCQ_SINGLE':
        q['options'] = data.get('options', q['options'])
        
    database.save_test(test_id, test_data.get('test_name', 'Test'), test_data, current_user.id)
    return jsonify({'success': True})


@app.route('/api/test/<test_id>', methods=['PATCH'])
@login_required
def rename_test(test_id):
    """Rename a test."""
    data = request.get_json(silent=True) or {}
    new_name = (data.get('name') or '').strip()
    if not new_name:
        return jsonify({'error': 'Name cannot be empty'}), 400
    test = database.get_test(test_id, current_user.id)
    if not test:
        return jsonify({'error': 'Not found'}), 404
    test['test_name'] = new_name
    database.save_test(test_id, new_name, test, current_user.id)
    return jsonify({'success': True, 'name': new_name})


@app.route('/api/finalize/<test_id>', methods=['POST'])
@login_required
def finalize_test(test_id):
    """Mark a draft test as ready to take."""
    test = database.get_test(test_id, current_user.id)
    if not test:
        return jsonify({'error': 'Not found'}), 404
    test['status'] = 'ready'
    database.save_test(test_id, test.get('test_name', 'Test'), test, current_user.id)
    return jsonify({'success': True, 'test_url': f'/test/{test_id}'})


@app.route('/test/<test_id>')
@login_required
def test_page(test_id):
    if not database.get_test(test_id, current_user.id):
        return redirect(url_for('index'))
    return render_template('test.html', test_id=test_id)


@app.route('/api/test/<test_id>')
@login_required
def get_test_data(test_id):
    test = database.get_test(test_id, current_user.id)
    if not test:
        return jsonify({"error": "Not found"}), 404
    # Strip correct answers, keep diagram info
    safe_q = []
    for q in test.get('questions', []):
        sq = {k: v for k, v in q.items() if k != 'correct_answer'}
        safe_q.append(sq)
    return jsonify({"test_id": test_id, "test_name": test.get('test_name', 'Test'),
                    "total_questions": len(safe_q), "subjects": test.get('subjects', []),
                    "duration_minutes": test.get('duration_minutes', 180), "questions": safe_q,
                    "image_info": test.get('image_info', [])})


@app.route('/api/submit', methods=['POST'])
@login_required
def submit_test():
    data = request.get_json(silent=True) or {}
    test_id = data.get('test_id')
    user_answers = data.get('answers', {})
    time_taken = data.get('time_taken_seconds', 0)

    test = database.get_test(test_id, current_user.id)
    if not test:
        return jsonify({"error": "Not found"}), 404

    questions = test.get('questions', [])
    results = []
    total_score = max_score = correct_count = incorrect_count = unattempted_count = 0
    wrong_questions = []
    subject_scores = {}

    for q in questions:
        qid = str(q['id'])
        correct = q.get('correct_answer', '')
        user_ans = user_answers.get(qid, '')
        mc = q.get('marks_correct', 4)
        mi = q.get('marks_incorrect', -1)
        max_score += mc
        subj = q.get('subject', 'General')
        if subj not in subject_scores:
            subject_scores[subj] = {'correct': 0, 'incorrect': 0, 'unattempted': 0,
                                     'score': 0, 'max_score': 0, 'total': 0}
        subject_scores[subj]['total'] += 1
        subject_scores[subj]['max_score'] += mc

        r = {'id': q['id'], 'subject': subj, 'topic': q.get('topic', ''), 'text': q['text'],
             'type': q.get('type', 'MCQ_SINGLE'), 'options': q.get('options'),
             'correct_answer': correct, 'user_answer': user_ans,
             'marks_correct': mc, 'marks_incorrect': mi,
             'has_diagram': q.get('has_diagram', False),
             'diagram_crop': q.get('diagram_crop'),
             'page_number': q.get('page_number', 1)}

        if not user_ans:
            r['status'] = 'unattempted'; r['marks_obtained'] = 0; unattempted_count += 1
            subject_scores[subj]['unattempted'] += 1
            wrong_questions.append({'id': q['id'], 'user_answer': 'Not Attempted'})
        elif _check_answer(user_ans, correct, q.get('type', 'MCQ_SINGLE')):
            r['status'] = 'correct'; r['marks_obtained'] = mc; total_score += mc; correct_count += 1
            subject_scores[subj]['correct'] += 1; subject_scores[subj]['score'] += mc
        else:
            r['status'] = 'incorrect'; r['marks_obtained'] = mi; total_score += mi; incorrect_count += 1
            subject_scores[subj]['incorrect'] += 1; subject_scores[subj]['score'] += mi
            wrong_questions.append({'id': q['id'], 'user_answer': user_ans})
        results.append(r)

    try:
        analysis = analyze_concepts(wrong_questions, questions)
    except Exception as e:
        analysis = {"summary": f"Analysis unavailable: {e}", "subject_analysis": {},
                    "recommendations": ["Review wrong answers manually."],
                    "priority_topics": [], "common_mistakes": []}

    result_id = str(uuid.uuid4())[:8]
    result_data = {
        'result_id': result_id, 'test_id': test_id,
        'test_name': test.get('test_name', 'Test'),
        'total_score': total_score, 'max_score': max_score,
        'correct': correct_count, 'incorrect': incorrect_count, 'unattempted': unattempted_count,
        'total_questions': len(questions),
        'accuracy': round((correct_count / max(correct_count + incorrect_count, 1)) * 100, 1),
        'percentage': round((total_score / max(max_score, 1)) * 100, 1),
        'time_taken_seconds': time_taken, 'subject_scores': subject_scores,
        'questions': results, 'analysis': analysis, 'submitted_at': datetime.now().isoformat()
    }
    
    database.save_attempt(result_id, test_id, test.get('test_name', 'Test'), total_score, max_score, result_data, current_user.id)
    return jsonify(result_data)


@app.route('/result/<result_id>')
@login_required
def result_page(result_id):
    if not database.get_attempt(result_id, current_user.id):
        return redirect(url_for('index'))
    return render_template('result.html', result_id=result_id)


@app.route('/api/result/<result_id>')
@login_required
def get_result_data(result_id):
    attempt = database.get_attempt(result_id, current_user.id)
    if not attempt:
        return jsonify({"error": "Not found"}), 404
    return jsonify(attempt)


# ======================================================================
# NEW ROUTES: Dashboard, Mistakes, Delete, Import/Export
# ======================================================================

@app.route('/api/dashboard')
@login_required
def get_dashboard_data():
    uid = current_user.id
    return jsonify({
        "tests": database.get_all_tests(uid),
        "attempts": database.get_all_attempts(uid),
        "user_name": current_user.name
    })

@app.route('/api/attempt/<attempt_id>', methods=['DELETE'])
@login_required
def delete_attempt_route(attempt_id):
    database.delete_attempt(attempt_id, current_user.id)
    return jsonify({"success": True})

@app.route('/api/test/<test_id>', methods=['DELETE'])
@login_required
def delete_test_route(test_id):
    database.delete_test(test_id, current_user.id)
    img_dir = os.path.join(IMAGE_DIR, test_id)
    if os.path.exists(img_dir):
        import shutil
        shutil.rmtree(img_dir, ignore_errors=True)
    return jsonify({"success": True})

@app.route('/mistakes')
@login_required
def mistakes_page():
    return render_template('mistakes.html')

@app.route('/api/mistakes')
@login_required
def get_mistakes_data():
    return jsonify(database.get_all_mistakes(current_user.id))

@app.route('/api/export')
@login_required
def export_data():
    uid = current_user.id
    return jsonify({
        "version": 1,
        "tests": [database.get_test(t['id'], uid) for t in database.get_all_tests(uid)],
        "attempts": [database.get_attempt(a['id'], uid) for a in database.get_all_attempts(uid)]
    })

@app.route('/api/import', methods=['POST'])
@login_required
def import_data():
    try:
        data = request.get_json(silent=True) or {}
        if not data or 'tests' not in data:
            return jsonify({"error": "Invalid format"}), 400
        
        imported_tests = 0
        imported_attempts = 0
        test_id_map = {}
        
        for t in data.get('tests', []):
            if t and 'test_id' in t:
                old_id = t['test_id']
                new_id = str(uuid.uuid4())[:8]
                t['test_id'] = new_id
                test_id_map[old_id] = new_id
                database.save_test(new_id, t.get('test_name', 'Imported Test'), t, current_user.id)
                imported_tests += 1
                
        for a in data.get('attempts', []):
            if a and 'result_id' in a and 'test_id' in a:
                new_result_id = str(uuid.uuid4())[:8]
                a['result_id'] = new_result_id
                if a['test_id'] in test_id_map:
                    a['test_id'] = test_id_map[a['test_id']]
                database.save_attempt(new_result_id, a['test_id'], a.get('test_name', 'Imported'),
                                      a.get('total_score', 0), a.get('max_score', 0), a, current_user.id)
                imported_attempts += 1
                
        return jsonify({"success": True, "tests": imported_tests, "attempts": imported_attempts})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == '__main__':
    print(f"[Boot] Loaded {len(API_KEYS)} API keys for rotation")
    print(f"[Boot] Request delay: {REQUEST_DELAY}s between Gemini calls")
    # Bind to 0.0.0.0 for deployment
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5050)), debug=False, use_reloader=False)
