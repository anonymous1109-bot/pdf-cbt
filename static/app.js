/**
 * PDF-to-CBT Engine — Interactive Test Interface
 * Supports: MCQ Single, MCQ Multi, Integer, Diagrams
 */

let testData = null;
let currentQ = 0;
let answers = {};
let marked = {};
let visited = {};
let timerSeconds = 0;
let timerInterval = null;
let startTime = null;
let activeSubject = 'all';

function escapeHTML(str) {
    if (!str) return '';
    return String(str).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#039;');
}

async function initCBT(testId) {
    try {
        const res = await fetch(`/api/test/${testId}`);
        if (res.redirected || res.status === 401) {
            window.location.href = '/login';
            return;
        }
        testData = await res.json();
        if (testData.error) { alert(testData.error); return; }

        document.getElementById('testName').textContent = testData.test_name || 'Test';
        timerSeconds = (testData.duration_minutes || 180) * 60;
        startTime = Date.now();

        buildSubjectTabs();
        buildNavGrid();
        showQuestion(0);
        startTimer();
        setupKeyboard();

        document.getElementById('submitTestBtn').onclick = showSubmitModal;
        document.getElementById('cancelSubmit').onclick = () =>
            document.getElementById('submitModal').classList.remove('active');
        document.getElementById('confirmSubmit').onclick = () => submitTest(testId);
    } catch (e) {
        alert('Failed to load test: ' + e.message);
    }
}

function buildSubjectTabs() {
    const tabs = document.getElementById('subjectTabs');
    const subjects = ['All', ...(testData.subjects || [])];
    tabs.innerHTML = subjects.map(s =>
        `<div class="subject-tab ${s === 'All' ? 'active' : ''}" data-subject="${s.toLowerCase()}">${s}</div>`
    ).join('');
    tabs.querySelectorAll('.subject-tab').forEach(tab => {
        tab.onclick = () => {
            tabs.querySelectorAll('.subject-tab').forEach(t => t.classList.remove('active'));
            tab.classList.add('active');
            activeSubject = tab.dataset.subject;
            buildNavGrid();
            const filtered = getFilteredQuestions();
            if (filtered.length > 0) showQuestion(testData.questions.indexOf(filtered[0]));
        };
    });
}

function getFilteredQuestions() {
    if (activeSubject === 'all') return testData.questions;
    return testData.questions.filter(q => q.subject.toLowerCase() === activeSubject);
}

function buildNavGrid() {
    const grid = document.getElementById('navGrid');
    const questions = activeSubject === 'all' ? testData.questions : getFilteredQuestions();
    grid.innerHTML = questions.map(q => {
        const idx = testData.questions.indexOf(q);
        const qid = String(q.id);
        let cls = 'not-visited';
        if (answers[qid]) cls = 'answered';
        else if (visited[qid]) cls = 'not-answered';
        if (marked[qid]) cls = 'marked';
        if (idx === currentQ) cls += ' current';
        return `<button class="nav-btn ${cls}" onclick="showQuestion(${idx})">${q.id}</button>`;
    }).join('');
}

function showQuestion(idx) {
    if (idx < 0 || idx >= testData.questions.length) return;
    currentQ = idx;
    const q = testData.questions[idx];
    const qid = String(q.id);
    visited[qid] = true;

    const panel = document.getElementById('questionPanel');
    const subjClass = (q.subject || '').toLowerCase();
    const qType = q.type || 'MCQ_SINGLE';

    // Diagram — show only the precisely cropped region (no full-page dumps)
    let diagramHTML = '';
    if (q.has_diagram && q.diagram_crop) {
        diagramHTML = `<div class="diagram-section">
            <img src="/static/test_images/${testData.test_id}/${q.diagram_crop}" 
                 alt="Figure for Q${q.id}" class="diagram-img"
                 onerror="this.parentElement.style.display='none'">
        </div>`;
    }


    // Type badge
    let typeBadge = '';
    if (qType === 'INTEGER') {
        typeBadge = '<span class="q-type-badge integer">INTEGER</span>';
    } else if (qType === 'MCQ_MULTI') {
        typeBadge = '<span class="q-type-badge multi">MULTI-SELECT</span>';
    }

    // Options or integer input
    let answerHTML = '';
    if (qType === 'INTEGER') {
        answerHTML = `
            <div class="integer-section">
                <label class="integer-label">Enter your numerical answer:</label>
                <input type="text" class="integer-input" id="integerAnswer" 
                    value="${answers[qid] || ''}" placeholder="Type answer (e.g. 42, -3.5)"
                    oninput="handleIntegerInput('${qid}', this.value)"
                    autocomplete="off">
                <div class="integer-hint">Accepted: integers, decimals, negative numbers</div>
            </div>`;
    } else if (q.options) {
        const selectedAns = answers[qid] || '';
        const selectedSet = qType === 'MCQ_MULTI'
            ? new Set(selectedAns.split(',').filter(Boolean))
            : new Set([selectedAns]);

        answerHTML = '<div class="options-list">' + Object.entries(q.options).map(([key, val]) => {
            const sel = selectedSet.has(key) ? 'selected' : '';
            return `<div class="option ${sel}" onclick="selectOption('${qid}','${key}','${qType}')">
                <div class="option-marker">${escapeHTML(key)}</div>
                <div class="option-text">${escapeHTML(val)}</div>
            </div>`;
        }).join('') + '</div>';

        if (qType === 'MCQ_MULTI') {
            answerHTML += '<div class="multi-hint">💡 Select one or more correct options</div>';
        }
    }

    const isMarked = marked[qid];
    panel.innerHTML = `
        <div class="q-number">Question ${q.id} of ${testData.total_questions}
            <span class="q-subject-badge ${subjClass}">${escapeHTML(q.subject)}</span>
            ${typeBadge}
            <span style="margin-left:0.5rem;font-size:0.7rem;color:var(--text-muted)">${escapeHTML(q.topic)}</span>
            <span style="float:right;font-size:0.75rem;color:var(--text-muted)">
                +${q.marks_correct || 4} / ${q.marks_incorrect || -1}
            </span>
        </div>
        <div class="q-text">${escapeHTML(q.text)}</div>
        ${diagramHTML}
        ${answerHTML}
        <div class="q-actions">
            <button class="btn btn-sm btn-clear" onclick="clearAnswer('${qid}')">✕ Clear</button>
            <button class="btn btn-sm btn-mark ${isMarked ? 'marked' : ''}" onclick="toggleMark('${qid}')">
                ${isMarked ? '★ Marked' : '☆ Mark for Review'}
            </button>
            <div style="flex:1"></div>
            ${idx > 0 ? '<button class="btn btn-sm btn-nav" onclick="showQuestion(' + (idx - 1) + ')">← Prev</button>' : ''}
            ${idx < testData.questions.length - 1 ? '<button class="btn btn-sm btn-nav" onclick="showQuestion(' + (idx + 1) + ')">Next →</button>' : ''}
        </div>`;

    // Focus integer input if applicable
    if (qType === 'INTEGER') {
        setTimeout(() => {
            const inp = document.getElementById('integerAnswer');
            if (inp) inp.focus();
        }, 100);
    }

    buildNavGrid();
    mathRetries = 0;
    renderMath();
}

function handleIntegerInput(qid, value) {
    let cleaned = value.replace(/[^0-9.\-]/g, '');
    if (cleaned.lastIndexOf('-') > 0) cleaned = (cleaned.startsWith('-') ? '-' : '') + cleaned.replace(/-/g, '');
    const parts = cleaned.split('.');
    if (parts.length > 2) cleaned = parts[0] + '.' + parts.slice(1).join('');

    const inp = document.getElementById('integerAnswer');
    if (inp) inp.value = cleaned;
    if (cleaned && cleaned !== '-') {
        answers[qid] = cleaned;
    } else {
        delete answers[qid];
    }
    buildNavGrid();
}

function selectOption(qid, key, type) {
    if (type === 'MCQ_MULTI') {
        let current = new Set((answers[qid] || '').split(',').filter(Boolean));
        if (current.has(key)) current.delete(key);
        else current.add(key);
        const val = [...current].sort().join(',');
        if (val) answers[qid] = val;
        else delete answers[qid];
    } else {
        answers[qid] = key;
    }
    showQuestion(currentQ);
}

function clearAnswer(qid) {
    delete answers[qid];
    showQuestion(currentQ);
}

function toggleMark(qid) {
    marked[qid] = !marked[qid];
    showQuestion(currentQ);
}

function startTimer() {
    updateTimerDisplay();
    timerInterval = setInterval(() => {
        timerSeconds--;
        if (timerSeconds <= 0) {
            clearInterval(timerInterval);
            alert('⏰ Time\'s up! Auto-submitting...');
            submitTest(testData.test_id);
            return;
        }
        if (timerSeconds <= 300) document.getElementById('timer').classList.add('warning');
        updateTimerDisplay();
    }, 1000);
}

function updateTimerDisplay() {
    const h = Math.floor(timerSeconds / 3600);
    const m = Math.floor((timerSeconds % 3600) / 60);
    const s = timerSeconds % 60;
    document.getElementById('timer').textContent =
        `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
}

function setupKeyboard() {
    document.addEventListener('keydown', e => {
        if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;
        if (e.key === 'ArrowRight' || e.key === 'n') showQuestion(currentQ + 1);
        if (e.key === 'ArrowLeft' || e.key === 'p') showQuestion(currentQ - 1);
        if (e.key === 'm') toggleMark(String(testData.questions[currentQ].id));
        if (['a', 'b', 'c', 'd'].includes(e.key.toLowerCase())) {
            const q = testData.questions[currentQ];
            if (q.options && q.type !== 'INTEGER') {
                selectOption(String(q.id), e.key.toUpperCase(), q.type);
            }
        }
    });
}

function showSubmitModal() {
    const total = testData.questions.length;
    const answered = Object.keys(answers).filter(k => answers[k]).length;
    const markedCount = Object.keys(marked).filter(k => marked[k]).length;

    document.getElementById('modalStats').innerHTML = `
        <div class="modal-stat">
            <div class="modal-stat-val" style="color:var(--green)">${answered}</div>
            <div class="modal-stat-label">Answered</div>
        </div>
        <div class="modal-stat">
            <div class="modal-stat-val" style="color:var(--red)">${total - answered}</div>
            <div class="modal-stat-label">Unanswered</div>
        </div>
        <div class="modal-stat">
            <div class="modal-stat-val" style="color:var(--purple)">${markedCount}</div>
            <div class="modal-stat-label">Marked</div>
        </div>`;
    document.getElementById('submitModal').classList.add('active');
}

async function submitTest(testId) {
    document.getElementById('submitModal').classList.remove('active');
    document.getElementById('submitting').classList.add('active');
    clearInterval(timerInterval);

    const timeTaken = Math.round((Date.now() - startTime) / 1000);
    try {
        const res = await fetch('/api/submit', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ test_id: testId, answers, time_taken_seconds: timeTaken })
        });
        if (res.redirected || res.status === 401) {
            window.location.href = '/login';
            return;
        }
        const data = await res.json();
        if (data.result_id) {
            sessionStorage.setItem('lastResult', JSON.stringify(data));
            window.location.href = `/result/${data.result_id}`;
        } else {
            alert('Error: ' + (data.error || 'Submission failed'));
            document.getElementById('submitting').classList.remove('active');
        }
    } catch (e) {
        alert('Network error: ' + e.message);
        document.getElementById('submitting').classList.remove('active');
    }
}

let mathRetries = 0;
function renderMath() {
    if (typeof renderMathInElement !== 'undefined') {
        renderMathInElement(document.getElementById('questionPanel'), {
            delimiters: [
                { left: '$$', right: '$$', display: true },
                { left: '$', right: '$', display: false },
                { left: '\\(', right: '\\)', display: false },
                { left: '\\[', right: '\\]', display: true }
            ],
            throwOnError: false
        });
    } else if (mathRetries++ < 20) {
        setTimeout(renderMath, 200);
    }
}
