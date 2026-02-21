// --- Upload Manager ---
const uploadManager = {
    queue: [], // { id, file, status: 'pending'|'uploading'|'success'|'error', progress: 0 }
    activeUploads: 0,
    maxConcurrent: 3,
    batchId: null,
    gatheringFiles: false,

    init() {
        this.bindDropEvents();
        this.renderUploadManagerUI();
    },

    bindDropEvents() {
        // Fullscreen drop overlay
        const overlay = document.createElement('div');
        overlay.className = 'drop-overlay';
        overlay.innerHTML = `
            <i class="fa-solid fa-cloud-arrow-up"></i>
            <h2>Drop files or folders to upload</h2>
        `;
        document.body.appendChild(overlay);

        let dragCounter = 0;

        document.addEventListener('dragenter', (e) => {
            e.preventDefault();
            dragCounter++;
            // Don't show global overlay if we're on the upload page's native dropzone
            if (e.target.closest && e.target.closest('.upload-dropzone')) return;

            if (state.user && state.user.role !== 'guest') {
                overlay.classList.add('active');
            }
        });

        document.addEventListener('dragleave', (e) => {
            e.preventDefault();
            dragCounter--;
            if (dragCounter === 0) {
                overlay.classList.remove('active');
            }
        });

        document.addEventListener('dragover', (e) => {
            e.preventDefault(); // Needed to allow drop
        });

        document.addEventListener('drop', async (e) => {
            e.preventDefault();
            dragCounter = 0;
            overlay.classList.remove('active');

            // Allow specialized handler if dropped straight into the upload page dropzone
            if (e.target.closest && e.target.closest('.upload-dropzone')) return;

            if (!state.user || state.user.role === 'guest') return;
            this.handleDropEvent(e);
        });
    },

    setNewBatchId() {
        const now = new Date();
        const format = n => n.toString().padStart(2, '0');
        this.batchId = `${now.getFullYear()}${format(now.getMonth() + 1)}${format(now.getDate())}_${format(now.getHours())}${format(now.getMinutes())}${format(now.getSeconds())}`;
    },

    async handleDropEvent(e) {
        this.gatheringFiles = true;
        if (!this.batchId) this.setNewBatchId();

        const entries = [];
        const fallbackFiles = [];

        const items = e.dataTransfer.items;
        if (items) {
            for (let i = 0; i < items.length; i++) {
                const item = items[i];
                if (item.kind === 'file') {
                    // Cache the entry synchronously so it isn't lost if we await
                    const entry = item.webkitGetAsEntry ? item.webkitGetAsEntry() : null;
                    if (entry) {
                        entries.push(entry);
                    } else {
                        // Fallback
                        fallbackFiles.push(item.getAsFile());
                    }
                }
            }
        } else {
            const files = e.dataTransfer.files;
            for (let i = 0; i < files.length; i++) {
                fallbackFiles.push(files[i]);
            }
        }

        for (const file of fallbackFiles) {
            if (file) this.addFileToQueue(file);
        }

        for (const entry of entries) {
            await this.traverseFileTree(entry);
        }

        this.gatheringFiles = false;
        this.processQueue();
        this.updateUI(); // run a check in case we finished already
    },

    handleFilesInput(filesList) {
        this.gatheringFiles = true;
        if (!this.batchId) this.setNewBatchId();

        for (let i = 0; i < filesList.length; i++) {
            this.addFileToQueue(filesList[i]);
        }

        this.gatheringFiles = false;
        this.processQueue();
        this.updateUI();
    },

    async traverseFileTree(item, path = '') {
        if (item.isFile) {
            await new Promise((resolve) => {
                item.file((file) => {
                    this.addFileToQueue(file);
                    resolve();
                }, () => resolve());
            });
        } else if (item.isDirectory) {
            const dirReader = item.createReader();
            const readEntries = async () => {
                return new Promise((resolve) => {
                    dirReader.readEntries(async (entries) => {
                        if (entries.length > 0) {
                            for (let i = 0; i < entries.length; i++) {
                                await this.traverseFileTree(entries[i], path + item.name + '/');
                            }
                            // Read more since readEntries is paginated sometimes
                            await readEntries();
                        }
                        resolve();
                    });
                });
            };
            await readEntries();
        }
    },

    addFileToQueue(file) {
        // Filter out non-media
        const ext = file.name.substring(file.name.lastIndexOf('.')).toLowerCase();
        const allowed = ['.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp', '.heic', '.mp4', '.mov', '.avi', '.mkv'];
        if (!allowed.includes(ext)) {
            console.log(`Skipping non-media file: ${file.name}`);
            return;
        }

        const id = Math.random().toString(36).substr(2, 9);
        this.queue.push({
            id,
            file,
            name: file.name,
            status: 'pending', // pending, uploading, success, error
            progress: 0
        });

        this.updateUI();
        this.processQueue(); // Start instantly instead of waiting for full traversal to complete
    },

    async processQueue() {
        if (this.activeUploads >= this.maxConcurrent) return;

        const next = this.queue.find(q => q.status === 'pending');
        if (!next) {
            // Queue empty/done
            return;
        }

        next.status = 'uploading';
        this.activeUploads++;
        this.updateUI();

        await this.uploadFile(next);

        this.activeUploads--;
        this.updateUI();
        this.processQueue(); // grab next
    },

    uploadFile(item) {
        return new Promise((resolve) => {
            const xhr = new XMLHttpRequest();
            const formData = new FormData();
            formData.append('file', item.file, item.file.name);
            formData.append('userid', state.user.userid);
            formData.append('upload_batch_id', this.batchId);

            xhr.upload.onprogress = (e) => {
                if (e.lengthComputable) {
                    item.progress = Math.round((e.loaded / e.total) * 100);
                    this.updateUI();
                }
            };

            xhr.onload = () => {
                if (xhr.status === 200) {
                    item.status = 'success';
                    item.progress = 100;
                } else {
                    item.status = 'error';
                }
                resolve();
            };

            xhr.onerror = () => {
                item.status = 'error';
                resolve();
            };

            xhr.open('POST', '/api/upload', true);
            xhr.send(formData);
        });
    },

    renderUploadManagerUI() {
        // We no longer render a floating popup overlay UI here. 
        // This is now natively built into the Uploads Tab inside app.js
    },

    hideOverlay(e) {
        // No-op
    },

    updateUI() {
        // Also update the Upload Tab UI if it's active
        this.updateTabUI();

        // Refresh views natively if finished
        const pending = this.queue.filter(q => q.status === 'pending' || q.status === 'uploading').length;
        if (pending === 0 && !this.gatheringFiles && this.batchId !== null) {
            // Optional: trigger a refresh of the views if current view is files or timeline
            if (state.view === 'files' || state.view === 'photos') {
                refreshViewData();
            }
            this.batchId = null; // Reset batch id to allow a new batch folder to be created
        }
    },

    bindToTabTable() {
        this.updateTabUI();
    },

    updateTabUI() {
        const tbody = document.getElementById('uploadTabTableBody');
        const headerStatus = document.getElementById('uploadTabHeaderStatus');

        if (!tbody) return; // View isn't active

        const total = this.queue.length;
        const pending = this.queue.filter(q => q.status === 'pending' || q.status === 'uploading').length;
        const success = this.queue.filter(q => q.status === 'success').length;

        if (headerStatus) {
            if (this.gatheringFiles) {
                headerStatus.textContent = "Scanning directories...";
            } else if (pending > 0) {
                const pct = total > 0 ? Math.round((success / total) * 100) : 0;
                headerStatus.textContent = `Uploading ${pending} files... (${pct}% complete of ${total})`;
            } else if (total > 0) {
                headerStatus.textContent = `Upload complete! (${success}/${total} successful)`;
            } else {
                headerStatus.textContent = "";
            }
        }

        if (this.queue.length === 0) {
            tbody.innerHTML = `<div class="upload-list-empty">No active uploads</div>`;
            return;
        }

        let html = '';
        for (let i = 0; i < this.queue.length; i++) {
            const q = this.queue[i];

            let icon = '<i class="fa-solid fa-spinner fa-spin" style="color: var(--accent-color);"></i>';
            let colorClass = 'status-default';
            if (q.status === 'pending') { icon = '<i class="fa-regular fa-clock" style="color: var(--text-secondary);"></i>'; colorClass = 'status-pending'; }
            if (q.status === 'success') { icon = '<i class="fa-solid fa-check" style="color: #34c759;"></i>'; colorClass = 'status-success'; }
            if (q.status === 'error') { icon = '<i class="fa-solid fa-circle-exclamation" style="color: #ff3b30;"></i>'; colorClass = 'status-error'; }

            let statusText = q.status;
            if (q.status === 'uploading') statusText = 'uploading';

            let progressHtml = '';
            if (q.status === 'uploading') {
                progressHtml = `
                    <div class="progress-bar-wrap">
                        <div class="progress-bar-bg">
                            <div class="progress-bar-fill" style="width: ${q.progress}%;"></div>
                        </div>
                        <span class="progress-pct">${q.progress}%</span>
                    </div>
                `;
            } else if (q.status === 'success') {
                progressHtml = `<span class="progress-pct success-text">100%</span>`;
            } else if (q.status === 'error') {
                progressHtml = `<span class="progress-pct error-text">Failed</span>`;
            } else {
                progressHtml = `<span class="progress-pct pending-text">0%</span>`;
            }

            html += `
                <div class="upload-list-item">
                    <div class="col-icon">${icon}</div>
                    <div class="col-name">${q.name}</div>
                    <div class="col-status ${colorClass}">${statusText}</div>
                    <div class="col-progress">${progressHtml}</div>
                </div>
            `;
        }
        tbody.innerHTML = html;
    }
};

// Initialize upload manager when document loads
document.addEventListener('DOMContentLoaded', () => {
    uploadManager.init();
});
