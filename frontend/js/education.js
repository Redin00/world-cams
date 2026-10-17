/**
 * WorldCams Security Awareness & Education Module
 * Renders technical guides, threat modeling articles, and interactive checklists.
 */

class EducationCenter {
  constructor() {
    this.navList = document.getElementById('education-articles-list');
    this.contentBody = document.getElementById('education-article-body');
    this.articles = [];
    this.activeArticleId = null;
    this.checklistState = JSON.parse(localStorage.getItem('worldcams_checklists') || '{}');

    this.init();
  }

  async init() {
    try {
      const res = await fetch('/api/education/articles');
      if (res.ok) {
        this.articles = await res.json();
        this.renderNav();
        if (this.articles.length > 0) {
          this.selectArticle(this.articles[0].id);
        }
      }
    } catch (err) {
      console.error('Failed to load education articles:', err);
    }
  }

  renderNav() {
    if (!this.navList) return;
    this.navList.innerHTML = '';

    this.articles.forEach(art => {
      const item = document.createElement('div');
      item.className = `education-nav-item ${art.id === this.activeArticleId ? 'active' : ''}`;
      item.innerHTML = `
        <div class="nav-item-cat">${art.category}</div>
        <div class="nav-item-title">${art.title}</div>
      `;
      item.addEventListener('click', () => this.selectArticle(art.id));
      this.navList.appendChild(item);
    });
  }

  selectArticle(id) {
    this.activeArticleId = id;
    this.renderNav();

    const art = this.articles.find(a => a.id === id);
    if (!art || !this.contentBody) return;

    // Render formatted markdown content
    let html = `
      <div style="margin-bottom: 1rem;">
        <span class="badge" style="background:rgba(6,182,212,0.1);color:var(--accent-cyan);padding:2px 8px;border-radius:4px;font-size:0.7rem;font-weight:700;">
          ${art.category.toUpperCase()}
        </span>
        <h2 style="font-size:1.3rem;font-weight:700;color:#fff;margin-top:0.4rem;">${art.title}</h2>
        <p style="font-size:0.85rem;color:var(--text-muted);margin-top:0.3rem;">${art.summary}</p>
      </div>
      <hr style="border:0;border-top:1px solid var(--border-subtle);margin-bottom:1.25rem;">
      <div class="article-markdown">
        ${this.parseMarkdown(art.content)}
      </div>
    `;

    // Render Checklists if present
    if (art.checklists && art.checklists.length > 0) {
      html += `
        <div style="margin-top: 1.5rem; background:var(--bg-secondary); border:1px solid var(--border-subtle); border-radius:6px; padding:1rem;">
          <h3 style="font-size:0.95rem;color:var(--accent-emerald);margin-bottom:0.75rem;display:flex;align-items:center;gap:0.4rem;">
            🛡️ Interactive Hardening Checklist
          </h3>
          <div class="checklist-items">
      `;

      art.checklists.forEach(chk => {
        const isChecked = Boolean(this.checklistState[chk.id]);
        html += `
          <div class="checklist-item-card" style="background:var(--bg-surface);border:1px solid var(--border-subtle);border-radius:4px;padding:0.6rem;margin-bottom:0.5rem;">
            <label style="display:flex;align-items:flex-start;gap:0.6rem;cursor:pointer;">
              <input type="checkbox" class="chk-toggle" data-id="${chk.id}" ${isChecked ? 'checked' : ''} style="margin-top:3px;">
              <div>
                <strong style="font-size:0.82rem;color:#fff;">${chk.title}</strong>
                <p style="font-size:0.75rem;color:var(--text-muted);margin:2px 0;">${chk.description}</p>
                ${chk.command_or_example ? `<code style="font-size:0.72rem;background:#000;color:var(--accent-cyan);padding:2px 6px;border-radius:3px;display:inline-block;margin-top:3px;">${chk.command_or_example}</code>` : ''}
              </div>
            </label>
          </div>
        `;
      });

      html += `</div></div>`;
    }

    this.contentBody.innerHTML = html;

    // Attach event listeners for checklist toggles
    this.contentBody.querySelectorAll('.chk-toggle').forEach(chkBox => {
      chkBox.addEventListener('change', (e) => {
        const chkId = e.target.dataset.id;
        this.checklistState[chkId] = e.target.checked;
        localStorage.setItem('worldcams_checklists', JSON.stringify(this.checklistState));
      });
    });

    lucide.createIcons();
  }

  parseMarkdown(text) {
    if (!text) return '';
    // Simple fast markdown parser for headers, lists, code, bold
    let html = text
      .replace(/^### (.*$)/gim, '<h3 style="color:#fff;font-size:1.05rem;margin:1rem 0 0.5rem;">$1</h3>')
      .replace(/^## (.*$)/gim, '<h2 style="color:#fff;font-size:1.2rem;margin:1.2rem 0 0.5rem;">$1</h2>')
      .replace(/^\- (.*$)/gim, '<li style="margin-left:1.2rem;margin-bottom:0.25rem;">$1</li>')
      .replace(/\*\*(.*?)\*\*/gim, '<strong style="color:#fff;">$1</strong>')
      .replace(/`([^`]+)`/gim, '<code>$1</code>');
    return html;
  }
}

window.educationCenter = new EducationCenter();
