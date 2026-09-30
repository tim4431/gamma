// The Background tasks API (routers/jobs.py): the account's jobs, and the
// POST /api/jobs/<route> that each kind of work starts with.
import { API, apiJson } from "../shared/lib/utils";
import { xhrUpload } from "../shared/lib/xhrUpload";

const job = (id) => `${API}/jobs/${encodeURIComponent(id)}`;
const json = (body) => ({ method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

export const listJobs = () => apiJson(`${API}/jobs`);
export const getJob = (id) => apiJson(job(id));
export const startJob = (route, body) => apiJson(`${API}/jobs/${route}`, json(body));
// A job over an uploaded file (a restore's zip): the upload's progress
// comes as onProgress(loaded, total), then the started job.
export const uploadJob = (route, form, options) => xhrUpload(`${API}/jobs/${route}`, form, options);
export const cancelJob = (id) => apiJson(`${job(id)}/cancel`, { method: "POST" });
export const dismissJob = (id) => apiJson(job(id), { method: "DELETE" });
export const clearJobs = () => apiJson(`${API}/jobs/clear`, { method: "POST" });

// A plain link: the browser's own download manager fetches the file (and
// shows its progress), however big it is.
export function downloadJob(task) {
  const a = document.createElement("a");
  a.href = `${job(task.id)}/download`;
  a.download = task.artifact?.name || "";
  document.body.appendChild(a);
  a.click();
  a.remove();
}
