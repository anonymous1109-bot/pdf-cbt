/**
 * Result Page — Analysis Dashboard
 */

let resultData = null;
let activeFilter = 'all';

async function initResult(resultId) {
    // Try sessionStorage first (instant), fallback to API
    const cached = sessionStorage.getItem('lastResult');
    if (cached) {
        resultData = JSON.parse(cached);
        sessionStorage.removeItem('lastResult');
    } else {
        try {
            const res = await fetch(`/api/result/${resultId}`);
            if (res.redirected || res.status === 401) {
                window.location.href = '/login';
                return;
            }
            resultData = await res.json();
            if (resultData.error) { alert(resultData.error); return; }
        } catch (e) { alert('Failed to load results'); return; }
    }
    renderScoreHero();
    renderSubjectBreakdown();
    renderAnalysis();
    renderQuestionReview();
    renderMathResult();
}

function renderScoreHero() {
    const d = resultData;
    const timeMins = Math.floor(d.time_taken_seconds / 60);
    document.getElementById('resultTitle').textContent = d.test_name + ' — Results';
    document.getElementById('resultSub').textContent = `Submitted at ${new Date(d.submitted_at).toLocaleString()}`;

    document.getElementById('scoreHero').innerHTML = `
        <div class="score-card">
            <div class="score-val main">${d.total_score}/${d.max_score}</div>
            <div class="score-label">Total Score</div>
        </div>
        <div class="score-card">
            <div class="score-val green">${d.correct}</div>
            <div class="score-label">Correct</div>
        </div>
        <div class="score-card">
            <div class="score-val red">${d.incorrect}</div>
            <div class="score-label">Incorrect</div>
        </div>
        <div class="score-card">
            <div class="score-val orange">${d.unattempted}</div>
            <div class="score-label">Unattempted</div>
        </div>
        <div class="score-card">
            <div class="score-val" style="color:var(--cyan)">${d.accuracy}%</div>
            <div class="score-label">Accuracy</div>
        </div>
        <div class="score-card">
            <div class="score-val" style="color:var(--text-secondary)">${timeMins}m</div>
            <div class="score-label">Time Taken</div>
        </div>`;
}

function renderSubjectBreakdown() {
    const container = document.getElementById('subjectBreakdown');
    const colors = { physics: '#3b82f6', chemistry: '#f59e0b', mathematics: '#8b5cf6', maths: '#8b5cf6' };

    container.innerHTML = Object.entries(resultData.subject_scores).map(([subj, s]) => {
        const pct = Math.max(0, Math.round((s.score / Math.max(s.max_score, 1)) * 100));
        const color = colors[subj.toLowerCase()] || '#6366f1';
        return `<div class="subj-card">
            <div class="subj-name" style="color:${color}">${subj}</div>
            <div class="subj-bar-wrap">
                <div class="subj-bar" style="width:${Math.max(0, pct)}%;background:${color}"></div>
            </div>
            <div class="subj-stats">
                <span>Score: ${s.score}/${s.max_score}</span>
                <span style="color:var(--green)">✓ ${s.correct}</span>
                <span style="color:var(--red)">✗ ${s.incorrect}</span>
                <span style="color:var(--orange)">○ ${s.unattempted}</span>
            </div>
        </div>`;
    }).join('');
}

function renderAnalysis() {
    const a = resultData.analysis;
    if (!a) return;
    const section = document.getElementById('analysisSection');

    let html = `<div class="analysis-card">
        <div class="analysis-summary">${a.summary || ''}</div>`;

    // Subject-wise analysis
    if (a.subject_analysis) {
        Object.entries(a.subject_analysis).forEach(([subj, sa]) => {
            html += `<div style="margin-bottom:1.25rem">
                <h4 style="margin-bottom:0.5rem">${subj}</h4>
                <p style="font-size:0.9rem;color:var(--text-secondary);margin-bottom:0.5rem">${sa.key_gaps || sa.score_comment || ''}</p>`;
            if (sa.weak_topics && sa.weak_topics.length) {
                html += `<p style="font-size:0.8rem"><span style="color:var(--red)">Weak:</span> ${sa.weak_topics.join(', ')}</p>`;
            }
            if (sa.strong_topics && sa.strong_topics.length) {
                html += `<p style="font-size:0.8rem"><span style="color:var(--green)">Strong:</span> ${sa.strong_topics.join(', ')}</p>`;
            }
            html += '</div>';
        });
    }
    html += '</div>';

    // Recommendations
    if (a.recommendations && a.recommendations.length) {
        html += `<div class="analysis-card">
            <h4 style="margin-bottom:1rem">💡 Recommendations</h4>
            <ul class="reco-list">${a.recommendations.map(r =>
                `<li class="reco-item"><span class="reco-icon">→</span><span class="reco-text">${r}</span></li>`
            ).join('')}</ul></div>`;
    }

    // Priority topics
    if (a.priority_topics && a.priority_topics.length) {
        html += `<div class="analysis-card"><h4 style="margin-bottom:1rem">🎯 Priority Topics</h4>`;
        a.priority_topics.forEach(pt => {
            html += `<div class="priority-card">
                <div class="priority-topic">${pt.topic}</div>
                <div class="priority-subject">${pt.subject}</div>
                <div class="priority-reason">${pt.reason || ''}</div>
                <div class="priority-plan">📖 ${pt.study_plan || ''}</div>
            </div>`;
        });
        html += '</div>';
    }

    // Common mistakes
    if (a.common_mistakes && a.common_mistakes.length) {
        html += `<div class="analysis-card"><h4 style="margin-bottom:1rem">⚠️ Common Mistake Patterns</h4>
            <ul class="reco-list">${a.common_mistakes.map(m =>
                `<li class="reco-item"><span class="reco-icon">⚡</span><span class="reco-text">${m}</span></li>`
            ).join('')}</ul></div>`;
    }

    section.innerHTML = html;
}

function renderQuestionReview() {
    const filterBar = document.getElementById('filterBar');
    filterBar.innerHTML = ['All', 'Correct', 'Incorrect', 'Unattempted'].map(f =>
        `<button class="filter-btn ${f.toLowerCase() === activeFilter ? 'active' : ''}" 
            onclick="setFilter('${f.toLowerCase()}')">${f}</button>`
    ).join('');

    const container = document.getElementById('questionReview');
    const questions = resultData.questions.filter(q =>
        activeFilter === 'all' || q.status === activeFilter
    );

    container.innerHTML = questions.map(q => {
        let optionsReview = '';
        if (q.options) {
            optionsReview = Object.entries(q.options).map(([k, v]) => {
                let style = '';
                if (k === q.correct_answer) style = 'color:var(--green);font-weight:600';
                else if (k === q.user_answer && q.status === 'incorrect') style = 'color:var(--red);text-decoration:line-through';
                return `<div style="font-size:0.85rem;margin-left:1rem;${style}">${k}. ${v}</div>`;
            }).join('');
        }

        const diagramHtml = q.diagram_crop
            ? `<img src="/static/test_images/${resultData.test_id}/${q.diagram_crop}" 
                   style="max-width:100%;max-height:250px;border-radius:8px;margin:0.5rem 0;background:#fff;padding:4px"
                   onerror="this.style.display='none'">`
            : '';

        return `<div class="review-q ${q.status}">
            <div class="review-q-header">
                <span class="review-q-num">Q${q.id} · ${q.subject} · ${q.topic || ''}</span>
                <span class="review-status ${q.status}">${q.status.toUpperCase()} (${q.marks_obtained >= 0 ? '+' : ''}${q.marks_obtained})</span>
            </div>
            <div class="review-q-text">${q.text}</div>
            ${diagramHtml}
            ${optionsReview}
            <div class="review-answers">
                <span class="review-correct">Correct: ${q.correct_answer}</span>
                <span class="review-user ${q.status === 'correct' ? 'is-correct' : ''}">Your Answer: ${q.user_answer || '—'}</span>
            </div>
        </div>`;
    }).join('');

    mathRetries = 0;
    renderMathResult();
}

function setFilter(f) {
    activeFilter = f;
    renderQuestionReview();
}

let mathRetries = 0;
function renderMathResult() {
    if (typeof renderMathInElement !== 'undefined') {
        renderMathInElement(document.getElementById('resultApp'), {
            delimiters: [
                { left: '$$', right: '$$', display: true },
                { left: '$', right: '$', display: false },
                { left: '\\(', right: '\\)', display: false },
                { left: '\\[', right: '\\]', display: true }
            ],
            throwOnError: false
        });
    } else if (mathRetries++ < 20) {
        setTimeout(renderMathResult, 200);
    }
}
