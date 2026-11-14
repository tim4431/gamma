// The Background tasks API (routers/jobs.py): the account's jobs, and the
// POST /api/jobs/<route> that each kind of work starts with. In a share view
// every call carries the share token (apiJson, withShare), and the jobs are
// the share's.
import { API, apiJson, withShare } from "../shared/lib/utils";
import { uploadFile } from "../shared/lib/uploadParts";
import { saveUrl } from "../shared/lib/saveFile.js";

const job = (id) => `${API}/jobs/${encodeURIComponent(id)}`;
const json = (body) => ({ method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

export const listJobs = () => apiJson(`${API}/jobs`);
export const getJob = (id) => apiJson(job(id));
export const startJob = (route, body) => apiJson(`${API}/jobs/${route}`, json(body));
// A job over an uploaded file (a restore's zip) and its form `fields`: one
// request, or a large file in parts at /jobs/<route>/parts
// (shared/lib/uploadParts.js). The upload's progress comes as
// onProgress(loaded, total), then the started job.
export const uploadJob = (route, file, fields, options) =>
  uploadFile(file, { url: `${API}/jobs/${route}`, parts: `${API}/jobs/${route}/parts`, fields }, options);
export const cancelJob = (id) => apiJson(`${job(id)}/cancel`, { method: "POST" });
// A job whose kind stops by a route of its own (taskKinds' stopPath).
export const stopAt = (path) => apiJson(`${API}${path}`, json({}));
export const dismissJob = (id) => apiJson(job(id), { method: "DELETE" });
export const clearJobs = () => apiJson(`${API}/jobs/clear`, { method: "POST" });

// A plain link: the browser's own download manager fetches the file (and
// shows its progress), however big it is; under an app that takes saves,
// the app fetches it (shared/lib/saveFile.js). The server counts the file
// fetched once it is (jobs.mark_downloaded), so this asks once. A share
// view's job is the share's, so its link carries the share token.
export const downloadJob = (task) => saveUrl(withShare(`${job(task.id)}/download`), task.artifact?.name || "");
