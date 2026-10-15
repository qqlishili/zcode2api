// 默认失败协议只允许类别和数字，禁止携带参数或 SDK 原文。
const CODES = {
  param_empty: [4, "extract"],
  param_too_short: [6, "extract"],
  param_missing_security_token: [6, "extract"],
  param_not_base64_json: [6, "extract"],
  solver_timeout: [2, "run"],
  solver_stalled: [2, "run"],
  sdk_init_failed: [3, "init"],
  sdk_failed: [4, "run"],
  sdk_on_error: [5, "run"],
  solver_unclassified: [4, "run"],
};

function failure(code, paramChars = null) {
  const err = new Error(Object.hasOwn(CODES, code) ? code : "solver_unclassified");
  err.solverCode = err.message;
  err.paramChars = paramChars;
  return err;
}

function extractVerifyParam(param) {
  let value = param;
  if (param && typeof param === "object") value = param.verifyParam || param.data || param.param;
  if (!value || String(value).length < 20) throw failure("param_empty", value ? String(value).length : 0);
  const str = String(value);
  if (str.length < 200) throw failure("param_too_short", str.length);
  let decoded;
  try { decoded = JSON.parse(Buffer.from(str, "base64").toString("utf8")); }
  catch (_) { throw failure("param_not_base64_json", str.length); }
  const token = decoded && (decoded.securityToken || decoded.SecurityToken);
  if (!token || String(token).length < 50) throw failure("param_missing_security_token", str.length);
  return str;
}

function makeDiagnostic(err, elapsedMs) {
  const code = err && Object.hasOwn(CODES, err.solverCode) ? err.solverCode : "solver_unclassified";
  const number = value => Number.isFinite(value) && value >= 0 ? Math.round(value) : null;
  return { v: 1, code, stage: CODES[code][1], exit_code: CODES[code][0],
    param_chars: number(err && err.paramChars), elapsed_ms: number(elapsedMs) };
}

function emitFailure(err, elapsedMs) {
  const diag = makeDiagnostic(err, elapsedMs);
  process.stderr.write("SOLVER_DIAG=" + JSON.stringify(diag) + "\n");
  return diag.exit_code;
}

module.exports = { extractVerifyParam, failure, makeDiagnostic, emitFailure };
