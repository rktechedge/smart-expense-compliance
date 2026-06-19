import os
import re
import json
import logging
from typing import Optional
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from google.adk.sessions.sqlite_session_service import SqliteSessionService
from google.adk.sessions import VertexAiSessionService

# --- Logging Setup ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("submission_frontend")

# --- Environment and Mode Setup ---
run_mode = os.environ.get("RUN_MODE", "local").lower()
is_local = (run_mode == "local")
LOCAL_AGENT_URL = os.environ.get("LOCAL_AGENT_URL", "http://localhost:8080")

if is_local:
    db_path = os.environ.get("LOCAL_SESSION_DB_PATH", "../ambient-expense-agent/expense_agent/.adk/session.db")
    db_path = os.path.abspath(db_path)
    # Ensure parent directory exists
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    session_service = SqliteSessionService(db_path=db_path)
    logger.info(f"Initialized in LOCAL mode. Using SQLite DB: {db_path}")
else:
    agent_runtime_id_raw = os.environ.get("AGENT_RUNTIME_ID", "")
    project_id = os.environ.get("PROJECT_ID") or os.environ.get("GOOGLE_CLOUD_PROJECT", "")
    location = os.environ.get("LOCATION") or os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")
    
    # Extract details if AGENT_RUNTIME_ID is a full resource name
    if "/" in agent_runtime_id_raw:
        parts = agent_runtime_id_raw.split("/")
        if len(parts) >= 6:
            project_id = parts[1]
            location = parts[3]
            agent_engine_id = parts[5]
        else:
            agent_engine_id = agent_runtime_id_raw
    else:
        agent_engine_id = agent_runtime_id_raw

    logger.info(f"Initialized in CLOUD mode. Project: {project_id}, Region: {location}, Agent Engine ID: {agent_engine_id}")

    session_service = VertexAiSessionService(
        project=project_id,
        location=location,
        agent_engine_id=agent_engine_id
    )

app = FastAPI(title="Manager Dashboard Service")

# --- Models ---
class ActionPayload(BaseModel):
    action: str
    interrupt_id: str
    user_id: str = "default-user"

# --- HTML Content ---
DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Manager Approval Dashboard</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;500;600;700&display=swap" rel="stylesheet">
    <style>
        :root {
            --bg-color: #080710;
            --card-bg: rgba(255, 255, 255, 0.03);
            --card-border: rgba(255, 255, 255, 0.05);
            --text-primary: #ffffff;
            --text-secondary: #a0a5c0;
            --accent-purple: #8257e5;
            --accent-green: #00e676;
            --accent-red: #ff1744;
            --accent-glow: rgba(130, 87, 229, 0.15);
        }

        * {
            box-sizing: border-box;
            margin: 0;
            padding: 0;
            font-family: 'Outfit', sans-serif;
            -webkit-font-smoothing: antialiased;
        }

        body {
            background-color: var(--bg-color);
            color: var(--text-primary);
            min-height: 100vh;
            overflow-x: hidden;
            position: relative;
        }

        /* Ambient Glow Background */
        .ambient-glow-1 {
            position: fixed;
            top: -10%;
            left: -10%;
            width: 50vw;
            height: 50vw;
            border-radius: 50%;
            background: radial-gradient(circle, var(--accent-glow) 0%, rgba(8, 7, 16, 0) 70%);
            z-index: -1;
            pointer-events: none;
        }

        .ambient-glow-2 {
            position: fixed;
            bottom: -10%;
            right: -10%;
            width: 50vw;
            height: 50vw;
            border-radius: 50%;
            background: radial-gradient(circle, rgba(0, 230, 118, 0.05) 0%, rgba(8, 7, 16, 0) 70%);
            z-index: -1;
            pointer-events: none;
        }

        header {
            padding: 2.5rem 4rem 1.5rem;
            display: flex;
            justify-content: space-between;
            align-image: center;
            border-bottom: 1px solid var(--card-border);
            backdrop-filter: blur(10px);
            align-items: center;
        }

        .header-title h1 {
            font-size: 2.2rem;
            font-weight: 700;
            letter-spacing: -0.5px;
            background: linear-gradient(135deg, #ffffff 50%, var(--text-secondary) 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }

        .header-title p {
            color: var(--text-secondary);
            font-size: 0.95rem;
            margin-top: 0.25rem;
        }

        .refresh-btn {
            background: var(--card-bg);
            border: 1px solid var(--card-border);
            color: var(--text-primary);
            padding: 0.75rem 1.5rem;
            border-radius: 12px;
            font-weight: 500;
            cursor: pointer;
            transition: all 0.3s cubic-bezier(0.16, 1, 0.3, 1);
            display: flex;
            align-items: center;
            gap: 0.5rem;
            backdrop-filter: blur(10px);
        }

        .refresh-btn:hover {
            background: rgba(255, 255, 255, 0.08);
            border-color: rgba(255, 255, 255, 0.2);
            transform: translateY(-2px);
        }

        .container {
            max-width: 1400px;
            margin: 2rem auto;
            padding: 0 2rem;
        }

        .grid {
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(400px, 1fr));
            gap: 2rem;
        }

        .card {
            background: var(--card-bg);
            border: 1px solid var(--card-border);
            border-radius: 24px;
            padding: 2rem;
            backdrop-filter: blur(12px);
            transition: all 0.4s cubic-bezier(0.16, 1, 0.3, 1);
            position: relative;
            overflow: hidden;
            display: flex;
            flex-direction: column;
            justify-content: space-between;
        }

        .card::before {
            content: '';
            position: absolute;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            background: linear-gradient(180deg, rgba(255, 255, 255, 0.02) 0%, rgba(255, 255, 255, 0) 100%);
            pointer-events: none;
        }

        .card:hover {
            transform: translateY(-6px);
            border-color: rgba(255, 255, 255, 0.12);
            box-shadow: 0 20px 40px rgba(0, 0, 0, 0.4);
        }

        .card-header {
            display: flex;
            justify-content: space-between;
            align-items: flex-start;
            margin-bottom: 1.5rem;
        }

        .submitter-info {
            display: flex;
            flex-direction: column;
        }

        .submitter {
            font-size: 1.1rem;
            font-weight: 600;
            color: var(--text-primary);
        }

        .date {
            font-size: 0.85rem;
            color: var(--text-secondary);
            margin-top: 0.2rem;
        }

        .amount {
            font-size: 1.8rem;
            font-weight: 700;
            color: var(--accent-green);
        }

        .details-list {
            list-style: none;
            margin-bottom: 2rem;
            flex-grow: 1;
        }

        .details-list li {
            display: flex;
            justify-content: space-between;
            padding: 0.6rem 0;
            border-bottom: 1px solid rgba(255, 255, 255, 0.02);
            font-size: 0.95rem;
        }

        .details-list li span:first-child {
            color: var(--text-secondary);
        }

        .details-list li span:last-child {
            font-weight: 500;
            color: var(--text-primary);
            text-align: right;
            max-width: 70%;
        }

        .card-actions {
            display: flex;
            gap: 1rem;
            margin-top: auto;
        }

        .btn {
            flex: 1;
            padding: 0.9rem;
            border-radius: 14px;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.3s cubic-bezier(0.16, 1, 0.3, 1);
            text-align: center;
            font-size: 0.95rem;
            display: flex;
            align-items: center;
            justify-content: center;
            position: relative;
        }

        .btn-view {
            background: rgba(255, 255, 255, 0.05);
            border: 1px solid rgba(255, 255, 255, 0.08);
            color: var(--text-primary);
            margin-bottom: 1rem;
            width: 100%;
        }

        .btn-view:hover {
            background: rgba(255, 255, 255, 0.1);
            border-color: rgba(255, 255, 255, 0.2);
        }

        .btn-approve {
            background: rgba(0, 230, 118, 0.1);
            border: 1px solid rgba(0, 230, 118, 0.25);
            color: var(--accent-green);
        }

        .btn-approve:hover {
            background: rgba(0, 230, 118, 0.2);
            border-color: rgba(0, 230, 118, 0.4);
            box-shadow: 0 0 15px rgba(0, 230, 118, 0.25);
        }

        .btn-reject {
            background: rgba(255, 23, 68, 0.1);
            border: 1px solid rgba(255, 23, 68, 0.25);
            color: var(--accent-red);
        }

        .btn-reject:hover {
            background: rgba(255, 23, 68, 0.2);
            border-color: rgba(255, 23, 68, 0.4);
            box-shadow: 0 0 15px rgba(255, 23, 68, 0.25);
        }

        /* Slide-out Drawer Modal */
        .drawer {
            position: fixed;
            top: 0;
            right: 0;
            width: 550px;
            max-width: 100%;
            height: 100%;
            background: rgba(10, 9, 18, 0.95);
            backdrop-filter: blur(20px);
            border-left: 1px solid var(--card-border);
            box-shadow: -20px 0 50px rgba(0, 0, 0, 0.6);
            z-index: 1000;
            transform: translateX(100%);
            transition: transform 0.4s cubic-bezier(0.16, 1, 0.3, 1);
            padding: 3rem 2.5rem;
            overflow-y: auto;
        }

        .drawer.open {
            transform: translateX(0);
        }

        .drawer-overlay {
            position: fixed;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            background: rgba(0, 0, 0, 0.5);
            backdrop-filter: blur(4px);
            z-index: 999;
            opacity: 0;
            pointer-events: none;
            transition: opacity 0.3s ease;
        }

        .drawer-overlay.visible {
            opacity: 1;
            pointer-events: auto;
        }

        .drawer-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 2.5rem;
        }

        .drawer-title {
            font-size: 1.6rem;
            font-weight: 700;
            background: linear-gradient(135deg, #ffffff 50%, var(--text-secondary) 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }

        .drawer-close {
            background: none;
            border: none;
            color: var(--text-secondary);
            font-size: 1.8rem;
            cursor: pointer;
            transition: color 0.2s;
        }

        .drawer-close:hover {
            color: var(--text-primary);
        }

        .report-content {
            font-size: 1rem;
            line-height: 1.7;
            color: var(--text-secondary);
        }

        .report-content h3 {
            color: var(--text-primary);
            font-size: 1.3rem;
            margin-top: 1.5rem;
            margin-bottom: 0.8rem;
            border-bottom: 1px solid rgba(255, 255, 255, 0.05);
            padding-bottom: 0.4rem;
        }

        .report-content p {
            margin-bottom: 1rem;
        }

        .report-content ul {
            margin-left: 1.5rem;
            margin-bottom: 1.5rem;
        }

        .report-content li {
            margin-bottom: 0.5rem;
        }

        /* Spinner & Loading States */
        .spinner {
            border: 3px solid rgba(255, 255, 255, 0.1);
            width: 24px;
            height: 24px;
            border-radius: 50%;
            border-left-color: var(--text-primary);
            animation: spin 1s linear infinite;
            display: inline-block;
        }

        @keyframes spin {
            0% { transform: rotate(0deg); }
            100% { transform: rotate(360deg); }
        }

        .card.loading::after {
            content: '';
            position: absolute;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            background: rgba(8, 7, 16, 0.7);
            backdrop-filter: blur(2px);
            z-index: 5;
            display: flex;
            align-items: center;
            justify-content: center;
        }

        .loading-overlay {
            position: absolute;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            background: rgba(8, 7, 16, 0.75);
            z-index: 10;
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            border-radius: 24px;
            opacity: 0;
            pointer-events: none;
            transition: opacity 0.3s ease;
        }

        .loading-overlay.active {
            opacity: 1;
            pointer-events: auto;
        }

        .loading-text {
            margin-top: 1rem;
            font-weight: 500;
            font-size: 0.95rem;
            color: var(--text-primary);
        }

        .empty-state {
            text-align: center;
            padding: 5rem 2rem;
            background: var(--card-bg);
            border: 1px solid var(--card-border);
            border-radius: 24px;
            grid-column: 1 / -1;
            backdrop-filter: blur(10px);
        }

        .empty-state h3 {
            font-size: 1.5rem;
            margin-bottom: 0.5rem;
            font-weight: 600;
        }

        .empty-state p {
            color: var(--text-secondary);
        }

        /* Success / Status Popups */
        .toast {
            position: fixed;
            bottom: 2rem;
            right: 2rem;
            background: rgba(130, 87, 229, 0.95);
            color: white;
            padding: 1rem 2rem;
            border-radius: 12px;
            font-weight: 500;
            box-shadow: 0 10px 30px rgba(0, 0, 0, 0.5);
            transform: translateY(100px);
            opacity: 0;
            transition: all 0.3s cubic-bezier(0.16, 1, 0.3, 1);
            z-index: 1001;
        }

        .toast.show {
            transform: translateY(0);
            opacity: 1;
        }
    </style>
</head>
<body>
    <div class="ambient-glow-1"></div>
    <div class="ambient-glow-2"></div>

    <header>
        <div class="header-title">
            <h1>Manager Approval Dashboard</h1>
            <p>Expense Approvals & Compliance Auditing Agent Runtime</p>
        </div>
        <button class="refresh-btn" onclick="loadPending()">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M21.5 2v6h-6M21.34 15.57a10 10 0 1 1-.57-8.38l5.67-5.67"/></svg>
            Refresh List
        </button>
    </header>

    <div class="container">
        <div class="grid" id="pending-grid">
            <div class="empty-state">
                <div class="spinner" style="margin-bottom: 1rem;"></div>
                <h3>Querying Agent Runtime...</h3>
                <p>Retrieving active sessions and scanning for pending manual reviews.</p>
            </div>
        </div>
    </div>

    <!-- Details/Report Drawer -->
    <div class="drawer-overlay" id="drawer-overlay" onclick="closeDrawer()"></div>
    <div class="drawer" id="drawer">
        <div class="drawer-header">
            <div class="drawer-title">Compliance Review</div>
            <button class="drawer-close" onclick="closeDrawer()">&times;</button>
        </div>
        <div class="report-content" id="report-content">
            <!-- Dynamically Rendered -->
        </div>
    </div>

    <!-- Alert Toast -->
    <div class="toast" id="toast">Action processed successfully</div>

    <script>
        let pendingList = [];

        async function loadPending() {
            const grid = document.getElementById('pending-grid');
            grid.innerHTML = `
                <div class="empty-state">
                    <div class="spinner" style="margin-bottom: 1rem;"></div>
                    <h3>Querying Agent Runtime...</h3>
                    <p>Retrieving active sessions and scanning for pending manual reviews.</p>
                </div>
            `;

            try {
                const response = await fetch('/api/pending');
                if (!response.ok) throw new Error('Failed to fetch pending approvals');
                pendingList = await response.json();
                
                if (pendingList.length === 0) {
                    grid.innerHTML = `
                        <div class="empty-state">
                            <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="var(--accent-green)" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" style="margin-bottom: 1rem;"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></svg>
                            <h3>All Clear</h3>
                            <p>No expenses are currently waiting for manager approval.</p>
                        </div>
                    `;
                    return;
                }

                grid.innerHTML = '';
                pendingList.forEach((item, index) => {
                    const card = document.createElement('div');
                    card.className = 'card';
                    card.id = `card-${item.session_id}`;
                    
                    const dateFormatted = item.date ? new Date(item.date).toLocaleDateString(undefined, {month: 'short', day: 'numeric', year: 'numeric'}) : 'N/A';
                    
                    card.innerHTML = `
                        <div class="loading-overlay" id="loader-${item.session_id}">
                            <div class="spinner"></div>
                            <div class="loading-text" id="loader-text-${item.session_id}">Processing Decision...</div>
                        </div>
                        <div>
                            <div class="card-header">
                                <div class="submitter-info">
                                    <span class="submitter">${item.submitter || 'Unknown Submitter'}</span>
                                    <span class="date">${dateFormatted}</span>
                                </div>
                                <span class="amount">$${parseFloat(item.amount || 0).toFixed(2)}</span>
                            </div>
                            <ul class="details-list">
                                <li>
                                    <span>Category</span>
                                    <span>${item.category || 'general'}</span>
                                </li>
                                <li>
                                    <span>Description</span>
                                    <span>${item.description || 'No description provided'}</span>
                                </li>
                                <li>
                                    <span>Agent Warning</span>
                                    <span style="color: #ff9100; font-weight: 500;">${item.message || 'Manual Review Needed'}</span>
                                </li>
                            </ul>
                        </div>
                        <div>
                            ${item.compliance_report ? `
                                <button class="btn btn-view" onclick="viewReport(${index})">
                                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="margin-right: 0.5rem; vertical-align: middle;"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>
                                    View Compliance Audit
                                </button>
                            ` : ''}
                            <div class="card-actions">
                                <button class="btn btn-approve" onclick="processApproval('${item.session_id}', '${item.interrupt_id}', 'approve', '${item.user_id}')">Approve</button>
                                <button class="btn btn-reject" onclick="processApproval('${item.session_id}', '${item.interrupt_id}', 'reject', '${item.user_id}')">Reject</button>
                            </div>
                        </div>
                    `;
                    grid.appendChild(card);

                    // Restore decision badge from localStorage if already decided
                    const savedDecision = localStorage.getItem(`decision_${item.session_id}`);
                    if (savedDecision) {
                        applyStatusBadge(item.session_id, savedDecision);
                    }
                });

                // Also render resolved cards from localStorage (approved/rejected sessions
                // disappear from /api/pending but we still want to show them)
                const pendingIds = new Set(pendingList.map(i => i.session_id));
                for (let i = 0; i < localStorage.length; i++) {
                    const key = localStorage.key(i);
                    if (!key || !key.startsWith('decision_')) continue;
                    const sid = key.replace('decision_', '');
                    if (pendingIds.has(sid)) continue; // already rendered above
                    const savedItem = localStorage.getItem(`item_${sid}`);
                    const savedDecision = localStorage.getItem(key);
                    if (!savedItem || !savedDecision) continue;
                    const item = JSON.parse(savedItem);
                    const card = document.createElement('div');
                    card.className = 'card';
                    card.id = `card-${sid}`;
                    const dateFormatted = item.date ? new Date(item.date).toLocaleDateString(undefined, {month: 'short', day: 'numeric', year: 'numeric'}) : 'N/A';
                    card.innerHTML = `
                        <div>
                            <div class="card-header">
                                <div class="submitter-info">
                                    <span class="submitter">${item.submitter || 'Unknown'}</span>
                                    <span class="date">${dateFormatted}</span>
                                </div>
                                <span class="amount">$${parseFloat(item.amount || 0).toFixed(2)}</span>
                            </div>
                            <ul class="details-list">
                                <li><span>Category</span><span>${item.category || 'general'}</span></li>
                                <li><span>Description</span><span>${item.description || ''}</span></li>
                            </ul>
                        </div>
                        <div><div class="card-actions"></div></div>
                    `;
                    grid.appendChild(card);
                    applyStatusBadge(sid, savedDecision);
                }

            } catch (err) {
                console.error(err);
                grid.innerHTML = `
                    <div class="empty-state">
                        <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="var(--accent-red)" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" style="margin-bottom: 1rem;"><rect x="3" y="3" width="18" height="18" rx="2" ry="2"/><line x1="9" y1="9" x2="15" y2="15"/><line x1="15" y1="9" x2="9" y2="15"/></svg>
                        <h3>Query Failed</h3>
                        <p>${err.message || 'Verify environment variables and credentials.'}</p>
                    </div>
                `;
            }
        }

        function viewReport(index) {
            const item = pendingList[index];
            const content = document.getElementById('report-content');
            
            // Basic markdown-like translation to html
            let htmlReport = item.compliance_report;
            if (htmlReport) {
                htmlReport = htmlReport.replace(/### (.*)/g, '<h3>$1</h3>');
                htmlReport = htmlReport.replace(/\\*\\*(.*?)\\*\\*/g, '<strong>$1</strong>');
                htmlReport = htmlReport.replace(/\\* (.*)/g, '<li>$1</li>');
                // Wrap bullet points
                if (htmlReport.includes('<li>')) {
                    // Quick wrapping
                    htmlReport = htmlReport.replace(/(<li>.*<\/li>)/gs, '<ul>$1</ul>');
                }
                htmlReport = htmlReport.replace(/\\n/g, '<br>');
            } else {
                htmlReport = '<p>No compliance review report was generated for this transaction.</p>';
            }

            content.innerHTML = htmlReport;

            document.getElementById('drawer').classList.add('open');
            document.getElementById('drawer-overlay').classList.add('visible');
        }

        function closeDrawer() {
            document.getElementById('drawer').classList.remove('open');
            document.getElementById('drawer-overlay').classList.remove('visible');
        }

        async function processApproval(sessionId, interruptId, action, userId) {
            const loader = document.getElementById(`loader-${sessionId}`);
            const loaderText = document.getElementById(`loader-text-${sessionId}`);
            loader.classList.add('active');
            loaderText.innerText = action === 'approve' ? 'Submitting Approval...' : 'Submitting Rejection...';

            try {
                const response = await fetch(`/api/action/${sessionId}`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ action: action, interrupt_id: interruptId, user_id: userId || 'default-user' })
                });

                if (!response.ok) throw new Error('Action failed to submit');
                const result = await response.json();

                // Hide loader
                loader.classList.remove('active');

                // Update card in-place with status badge, then persist
                applyStatusBadge(sessionId, action);
                // Save both the decision and the full card data for post-refresh rendering
                localStorage.setItem(`decision_${sessionId}`, action);
                const itemData = pendingList.find(i => i.session_id === sessionId);
                if (itemData) localStorage.setItem(`item_${sessionId}`, JSON.stringify(itemData));

                showToast(`Expense ${action === 'approve' ? 'Approved ✓' : 'Rejected ✗'} successfully!`);

                showToast(`Expense ${action === 'approve' ? 'Approved ✓' : 'Rejected ✗'} successfully!`);

            } catch (err) {
                console.error(err);
                showToast(`Failed: ${err.message}`);
                loader.classList.remove('active');
            }
        }

        function applyStatusBadge(sessionId, action) {
            const card = document.getElementById(`card-${sessionId}`);
            if (!card) return;
            const isApproved = action === 'approve';
            const statusColor = isApproved ? 'var(--accent-green)' : 'var(--accent-red)';
            const statusBg = isApproved ? 'rgba(0, 230, 118, 0.08)' : 'rgba(255, 23, 68, 0.08)';
            const statusBorder = isApproved ? 'rgba(0, 230, 118, 0.3)' : 'rgba(255, 23, 68, 0.3)';
            const statusIcon = isApproved ? '✓' : '✗';
            const statusLabel = isApproved ? 'Approved' : 'Rejected';
            const actionsDiv = card.querySelector('.card-actions');
            if (actionsDiv) {
                actionsDiv.innerHTML = `
                    <div style="width:100%;padding:0.9rem;border-radius:14px;background:${statusBg};border:1px solid ${statusBorder};color:${statusColor};font-weight:700;font-size:1.05rem;text-align:center;letter-spacing:0.5px;display:flex;align-items:center;justify-content:center;gap:0.5rem;">
                        <span style="font-size:1.2rem;">${statusIcon}</span>${statusLabel}
                    </div>`;
            }
            card.style.opacity = '0.75';
            card.style.transform = 'none';
        }

        function showToast(message) {
            const toast = document.getElementById('toast');
            toast.innerText = message;
            toast.classList.add('show');
            setTimeout(() => {
                toast.classList.remove('show');
            }, 3000);
        }

        // Initial load
        window.onload = loadPending;
    </script>
</body>
</html>
"""

# --- Endpoints ---
@app.get("/", response_class=HTMLResponse)
async def get_dashboard():
    return HTMLResponse(content=DASHBOARD_HTML)

@app.get("/api/pending")
async def get_pending():
    try:
        resp = await session_service.list_sessions(app_name="expense_agent")
        pending_items = []
        for s in resp.sessions:
            # Fetch the full session to get events
            session = await session_service.get_session(
                app_name="expense_agent",
                user_id=s.user_id,
                session_id=s.id
            )
            if not session:
                continue
            
            # Find unresolved adk_request_input function calls
            calls = {}
            responses = set()
            compliance_report = ""
            
            for ev in session.events:
                # Capture compliance report from review_agent text parts
                if ev.author == "review_agent" and ev.content and ev.content.parts:
                    for part in ev.content.parts:
                        if part.text:
                            compliance_report = part.text
                
                # Check for function calls/responses
                if ev.content and ev.content.parts:
                    for part in ev.content.parts:
                        fn_call = part.function_call
                        if fn_call and fn_call.name == "adk_request_input":
                            calls[fn_call.id] = {
                                "args": fn_call.args,
                                "timestamp": ev.timestamp
                            }
                        fn_resp = part.function_response
                        if fn_resp and fn_resp.name == "adk_request_input":
                            responses.add(fn_resp.id)
            
            unresolved_ids = set(calls.keys()) - responses
            for fid in unresolved_ids:
                call_info = calls[fid]
                args = call_info["args"]
                
                # Safeguard against Pydantic or non-dict args
                if not isinstance(args, dict):
                    args = getattr(args, "model_dump", lambda: {})() or getattr(args, "__dict__", {})
                
                payload = args.get("payload") or {}
                if not isinstance(payload, dict):
                    payload = getattr(payload, "model_dump", lambda: {})() or getattr(payload, "__dict__", {})
                
                # Build the item
                pending_items.append({
                    "session_id": session.id,
                    "user_id": session.user_id,
                    "interrupt_id": fid,
                    "amount": payload.get("amount"),
                    "submitter": payload.get("submitter"),
                    "category": payload.get("category"),
                    "description": payload.get("description"),
                    "date": payload.get("date"),
                    "message": args.get("message"),
                    "compliance_report": compliance_report,
                    "timestamp": call_info["timestamp"]
                })
        
        return pending_items
    except Exception as e:
        logger.exception("Error in /api/pending")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/action/{session_id}")
async def take_action(session_id: str, payload_data: ActionPayload):
    action = payload_data.action
    interrupt_id = payload_data.interrupt_id
    
    approved = (action == "approve")
    
    resume_payload = {
        "role": "user",
        "parts": [
            {
                "function_response": {
                    "id": interrupt_id,
                    "name": "adk_request_input",
                    "response": {
                        "decision": "approve" if approved else "reject"
                    }
                }
            }
        ]
    }
    
    try:
        import httpx
        decision = None
        message_text = ""
        
        if is_local:
            run_url = f"{LOCAL_AGENT_URL}/run_sse"
            payload = {
                "app_name": "expense_agent",
                "user_id": payload_data.user_id,
                "session_id": session_id,
                "new_message": resume_payload,
            }
            logger.info(f"Sending run_sse request to local agent at {run_url}")
            headers = {"Content-Type": "application/json"}
            async with httpx.AsyncClient(timeout=120.0) as client:
                async with client.stream("POST", run_url, headers=headers, json=payload) as response:
                    if response.status_code != 200:
                        error_content = await response.aread()
                        logger.error(f"HTTP {response.status_code} from local agent: {error_content.decode()}")
                        raise HTTPException(
                            status_code=response.status_code,
                            detail=f"Local agent error: {error_content.decode()}"
                        )
                    async for line in response.aiter_lines():
                        if not line or not line.startswith("data: "):
                            continue
                        data_str = line[len("data: ") :]
                        try:
                            event = json.loads(data_str)
                            content = event.get("content")
                            if isinstance(content, dict):
                                parts_list = content.get("parts", [])
                                for part in parts_list:
                                    if "text" in part:
                                        message_text += part["text"] + " "
                            
                            output = event.get("output")
                            if isinstance(output, dict):
                                status = output.get("status")
                                if status in ("approved", "rejected"):
                                    decision = status
                        except json.JSONDecodeError:
                            continue
        else:
            import google.auth
            import google.auth.transport.requests
            
            credentials, _ = google.auth.default(
                scopes=["https://www.googleapis.com/auth/cloud-platform"]
            )
            auth_req = google.auth.transport.requests.Request()
            credentials.refresh(auth_req)
            
            headers = {
                "Authorization": f"Bearer {credentials.token}",
                "Content-Type": "application/json",
            }
            
            # Construct endpoint url
            if "/" in agent_runtime_id_raw:
                full_engine_path = agent_runtime_id_raw
                parts = agent_runtime_id_raw.split("/")
                region = parts[3]
            else:
                region = location
                full_engine_path = f"projects/{project_id}/locations/{region}/reasoningEngines/{agent_runtime_id_raw}"
                
            stream_url = f"https://{region}-aiplatform.googleapis.com/v1/{full_engine_path}:streamQuery"
            
            # Use the actual user_id the session was created with
            session_user_id = payload_data.user_id
            logger.info(f"Resuming session {session_id} for user_id={session_user_id}")
            input_payload = {
                "user_id": session_user_id,
                "session_id": session_id,
                "message": resume_payload,
            }
            
            request_body = {
                "class_method": "async_stream_query",
                "input": input_payload,
            }
            
            logger.info(f"Sending streamQuery request to {stream_url}")
            
            async with httpx.AsyncClient(timeout=120.0) as client:
                async with client.stream("POST", stream_url, headers=headers, json=request_body) as response:
                    if response.status_code != 200:
                        error_content = await response.aread()
                        logger.error(f"HTTP {response.status_code} from Agent Runtime: {error_content.decode()}")
                        raise HTTPException(
                            status_code=response.status_code,
                            detail=f"Agent Runtime error: {error_content.decode()}"
                        )
                    
                    async for line in response.aiter_lines():
                        if not line:
                            continue
                        try:
                            event = json.loads(line)
                            content = event.get("content")
                            if isinstance(content, dict):
                                parts_list = content.get("parts", [])
                                for part in parts_list:
                                    if "text" in part:
                                        message_text += part["text"] + " "
                            
                            output = event.get("output")
                            if isinstance(output, dict):
                                status = output.get("status")
                                if status in ("approved", "rejected"):
                                    decision = status
                        except json.JSONDecodeError:
                            continue
        
        return {
            "status": "success",
            "decision": decision or ("approved" if approved else "rejected"),
            "message": message_text.strip()
        }
        
    except Exception as e:
        logger.exception("Error resuming session")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/pubsub")
async def receive_pubsub(request: Request):
    """
    Pub/Sub push endpoint. Pub/Sub delivers an OIDC-authenticated request here.
    This service then relays the expense payload to Agent Runtime using its own
    OAuth2 ADC credentials (roles/aiplatform.user), bridging the token type gap.
    """
    import base64
    import google.auth
    import google.auth.transport.requests
    import httpx

    try:
        body = await request.json()
    except Exception:
        logger.warning("Received non-JSON body on /api/pubsub")
        # Return 200 so Pub/Sub doesn't keep retrying a malformed message
        return {"status": "ignored", "reason": "non-json body"}

    # Pub/Sub wraps the message in an envelope: {"message": {"data": "<base64>", ...}}
    message = body.get("message", {})
    data_b64 = message.get("data", "")

    if not data_b64:
        logger.warning("Pub/Sub message has no data field")
        return {"status": "ignored", "reason": "no data"}

    try:
        raw = base64.b64decode(data_b64).decode("utf-8")
        logger.info(f"Pub/Sub raw payload: {raw}")
        payload = json.loads(raw)
    except Exception as e:
        logger.error(f"Failed to decode Pub/Sub message: {e}")
        return {"status": "ignored", "reason": f"decode error: {e}"}

    # Support both {"input": {"message": "..."}} and flat {"amount": ...} formats
    expense_str = None
    if "input" in payload and "message" in payload["input"]:
        expense_str = payload["input"]["message"]
    elif "message" in payload:
        expense_str = payload["message"]
    else:
        # Treat the whole payload as the expense dict
        expense_str = json.dumps(payload)

    logger.info(f"Forwarding expense to Agent Runtime: {expense_str}")

    # Build OAuth2 credentials (ADC) — these are proper Bearer tokens for Vertex AI
    try:
        credentials, _ = google.auth.default(
            scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )
        auth_req = google.auth.transport.requests.Request()
        credentials.refresh(auth_req)
    except Exception as e:
        logger.error(f"Failed to obtain ADC credentials: {e}")
        raise HTTPException(status_code=500, detail=f"Auth error: {e}")

    headers = {
        "Authorization": f"Bearer {credentials.token}",
        "Content-Type": "application/json",
    }

    # Build Agent Runtime endpoint
    if "/" in agent_runtime_id_raw:
        full_engine_path = agent_runtime_id_raw
        parts_path = agent_runtime_id_raw.split("/")
        region = parts_path[3]
    else:
        region = location
        full_engine_path = f"projects/{project_id}/locations/{region}/reasoningEngines/{agent_runtime_id_raw}"

    stream_url = f"https://{region}-aiplatform.googleapis.com/v1/{full_engine_path}:streamQuery"

    request_body = {
        "class_method": "async_stream_query",
        "input": {
            "user_id": "pubsub-user",
            "message": expense_str,
        },
    }

    logger.info(f"Calling Agent Runtime at {stream_url}")

    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            async with client.stream("POST", stream_url, headers=headers, json=request_body) as response:
                if response.status_code != 200:
                    error_content = await response.aread()
                    logger.error(f"Agent Runtime HTTP {response.status_code}: {error_content.decode()}")
                    # Return 200 to Pub/Sub so it doesn't dead-letter on agent errors;
                    # the agent itself logs the failure.
                    return {"status": "agent_error", "code": response.status_code}

                # Drain the stream (agent processes asynchronously)
                response_lines = []
                async for line in response.aiter_lines():
                    if line:
                        response_lines.append(line)
                        logger.info(f"Agent Runtime stream: {line[:200]}")

        logger.info(f"Agent Runtime processing complete. Lines received: {len(response_lines)}")
        return {"status": "ok", "lines": len(response_lines)}

    except Exception as e:
        logger.exception("Error forwarding to Agent Runtime")
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

