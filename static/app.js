const state = {
  token: sessionStorage.getItem("rag_demo_session") || "",
  documents: [],
};

const elements = {
  loginPanel: document.querySelector("#login-panel"),
  loginForm: document.querySelector("#login-form"),
  loginButton: document.querySelector("#login-button"),
  loginError: document.querySelector("#login-error"),
  accessCode: document.querySelector("#access-code"),
  workspace: document.querySelector("#workspace"),
  logoutButton: document.querySelector("#logout-button"),
  globalMessage: document.querySelector("#global-message"),
  uploadForm: document.querySelector("#upload-form"),
  uploadButton: document.querySelector("#upload-button"),
  pdfFile: document.querySelector("#pdf-file"),
  fileLabel: document.querySelector("#file-label"),
  documentSelect: document.querySelector("#document-select"),
  documentMeta: document.querySelector("#document-meta"),
  questionForm: document.querySelector("#question-form"),
  question: document.querySelector("#question"),
  askButton: document.querySelector("#ask-button"),
  answerEmpty: document.querySelector("#answer-empty"),
  answerResult: document.querySelector("#answer-result"),
  answerMode: document.querySelector("#answer-mode"),
  answerText: document.querySelector("#answer-text"),
  sourceCount: document.querySelector("#source-count"),
  sourcesList: document.querySelector("#sources-list"),
};

function setButtonLoading(button, loading, idleText, loadingText) {
  button.disabled = loading;
  button.textContent = loading ? loadingText : idleText;
}

function showMessage(message, type = "success") {
  elements.globalMessage.textContent = message;
  elements.globalMessage.className = type === "error" ? "message message-error" : "message";
  elements.globalMessage.hidden = false;
}

function clearMessage() {
  elements.globalMessage.hidden = true;
  elements.globalMessage.textContent = "";
}

function getErrorMessage(payload, fallback) {
  if (typeof payload?.detail === "string") {
    return payload.detail;
  }
  if (Array.isArray(payload?.detail) && payload.detail.length) {
    return "输入内容不符合接口要求，请检查后重试。";
  }
  return fallback;
}

async function apiRequest(path, options = {}, includeAuth = true) {
  const headers = new Headers(options.headers || {});
  if (includeAuth && state.token) {
    headers.set("Authorization", `Bearer ${state.token}`);
  }
  if (options.body && !(options.body instanceof FormData) && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }

  const response = await fetch(path, { ...options, headers });
  let payload = null;
  try {
    payload = await response.json();
  } catch {
    payload = null;
  }

  if (!response.ok) {
    if (response.status === 401 && includeAuth) {
      logout(false);
    }
    const error = new Error(getErrorMessage(payload, `请求失败（${response.status}）`));
    error.status = response.status;
    throw error;
  }
  return payload;
}

function showWorkspace() {
  elements.loginPanel.hidden = true;
  elements.workspace.hidden = false;
  elements.logoutButton.hidden = false;
}

function showLogin() {
  elements.workspace.hidden = true;
  elements.logoutButton.hidden = true;
  elements.loginPanel.hidden = false;
  elements.accessCode.focus();
}

function logout(showNotice = true) {
  state.token = "";
  state.documents = [];
  sessionStorage.removeItem("rag_demo_session");
  renderDocuments();
  showLogin();
  if (showNotice) {
    elements.loginError.textContent = "已退出当前演示会话。";
    elements.loginError.className = "message";
    elements.loginError.hidden = false;
  }
}

function selectedDocument() {
  return state.documents.find((item) => item.document_id === elements.documentSelect.value) || null;
}

function updateDocumentMeta() {
  const documentItem = selectedDocument();
  if (!documentItem) {
    elements.documentMeta.textContent = "暂无可用文档";
    elements.askButton.disabled = true;
    return;
  }
  elements.documentMeta.textContent = `${documentItem.pages} 页 · ${documentItem.chunk_count} 个文本块 · ${documentItem.embedding_model}`;
  elements.askButton.disabled = false;
}

function renderDocuments(preferredId = "") {
  const previousId = preferredId || elements.documentSelect.value;
  elements.documentSelect.replaceChildren();

  if (!state.documents.length) {
    const option = document.createElement("option");
    option.value = "";
    option.textContent = "请先上传 PDF";
    elements.documentSelect.append(option);
    elements.documentSelect.disabled = true;
    updateDocumentMeta();
    return;
  }

  for (const documentItem of state.documents) {
    const option = document.createElement("option");
    option.value = documentItem.document_id;
    option.textContent = documentItem.filename;
    elements.documentSelect.append(option);
  }
  elements.documentSelect.disabled = false;
  if (state.documents.some((item) => item.document_id === previousId)) {
    elements.documentSelect.value = previousId;
  }
  updateDocumentMeta();
}

async function loadDocuments(preferredId = "") {
  const payload = await apiRequest("/documents");
  state.documents = Array.isArray(payload.documents) ? payload.documents : [];
  renderDocuments(preferredId);
}

function renderSources(sources) {
  elements.sourcesList.replaceChildren();
  if (!sources.length) {
    const empty = document.createElement("p");
    empty.className = "muted";
    empty.textContent = "没有达到相关度阈值的引用原文。";
    elements.sourcesList.append(empty);
    return;
  }

  sources.forEach((source, index) => {
    const details = document.createElement("details");
    details.className = "source-card";
    if (index === 0) {
      details.open = true;
    }

    const summary = document.createElement("summary");
    const title = document.createElement("span");
    title.className = "source-title";
    title.textContent = `来源${index + 1} · ${source.filename} · 第 ${source.page} 页`;
    const score = document.createElement("span");
    score.className = "source-score";
    score.textContent = `相关度 ${Number(source.score).toFixed(3)}`;
    summary.append(title, score);

    const text = document.createElement("p");
    text.className = "source-text";
    text.textContent = source.text;
    details.append(summary, text);
    elements.sourcesList.append(details);
  });
}

function renderAnswer(payload) {
  const sources = Array.isArray(payload.sources) ? payload.sources : [];
  elements.answerEmpty.hidden = true;
  elements.answerResult.hidden = false;
  elements.answerMode.textContent = payload.mode === "insufficient_context"
    ? "资料不足"
    : "基于文档生成";
  elements.answerText.textContent = payload.answer || "未返回回答。";
  elements.sourceCount.textContent = `${sources.length} 条引用`;
  renderSources(sources);
}

elements.loginForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  elements.loginError.hidden = true;
  setButtonLoading(elements.loginButton, true, "进入系统", "正在验证…");
  try {
    const payload = await apiRequest(
      "/session",
      {
        method: "POST",
        body: JSON.stringify({ access_code: elements.accessCode.value }),
      },
      false,
    );
    state.token = payload.access_token;
    sessionStorage.setItem("rag_demo_session", state.token);
    elements.accessCode.value = "";
    showWorkspace();
    await loadDocuments();
  } catch (error) {
    elements.loginError.textContent = error.message;
    elements.loginError.className = "message message-error";
    elements.loginError.hidden = false;
  } finally {
    setButtonLoading(elements.loginButton, false, "进入系统", "正在验证…");
  }
});

elements.logoutButton.addEventListener("click", () => logout(true));

elements.pdfFile.addEventListener("change", () => {
  const file = elements.pdfFile.files[0];
  elements.fileLabel.textContent = file ? file.name : "选择文字型 PDF";
});

elements.uploadForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  clearMessage();
  const file = elements.pdfFile.files[0];
  if (!file) {
    showMessage("请先选择 PDF 文件。", "error");
    return;
  }
  if (file.size > 10 * 1024 * 1024) {
    showMessage("PDF 文件不能超过 10 MB。", "error");
    return;
  }

  const formData = new FormData();
  formData.append("file", file);
  setButtonLoading(elements.uploadButton, true, "上传并建立索引", "正在解析与向量化…");
  try {
    const payload = await apiRequest("/upload", { method: "POST", body: formData });
    await loadDocuments(payload.document_id);
    elements.pdfFile.value = "";
    elements.fileLabel.textContent = "选择文字型 PDF";
    showMessage(`《${payload.filename}》已建立索引，共 ${payload.chunk_count} 个文本块。`);
  } catch (error) {
    showMessage(error.message, "error");
  } finally {
    setButtonLoading(elements.uploadButton, false, "上传并建立索引", "正在解析与向量化…");
  }
});

elements.documentSelect.addEventListener("change", () => {
  updateDocumentMeta();
  elements.answerResult.hidden = true;
  elements.answerEmpty.hidden = false;
});

elements.questionForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  clearMessage();
  const documentItem = selectedDocument();
  const question = elements.question.value.trim();
  if (!documentItem) {
    showMessage("请先上传并选择一份文档。", "error");
    return;
  }
  if (!question) {
    showMessage("请输入问题。", "error");
    return;
  }

  setButtonLoading(elements.askButton, true, "开始问答", "正在检索并生成回答…");
  try {
    const payload = await apiRequest("/ask", {
      method: "POST",
      body: JSON.stringify({
        question,
        top_k: 3,
        document_id: documentItem.document_id,
        min_score: 0.25,
        use_rag: true,
      }),
    });
    renderAnswer(payload);
  } catch (error) {
    showMessage(error.message, "error");
  } finally {
    setButtonLoading(elements.askButton, false, "开始问答", "正在检索并生成回答…");
    updateDocumentMeta();
  }
});

async function initialize() {
  if (!state.token) {
    showLogin();
    return;
  }
  showWorkspace();
  try {
    await loadDocuments();
  } catch (error) {
    if (error.status !== 401) {
      showMessage(error.message, "error");
    }
  }
}

initialize();
