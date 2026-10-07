"""What went wrong, in words a user can act on. Never a stack trace; the original message is kept as `detail` for the audit view."""
from app.utils.errors import PipelineError

CATALOG: dict[str, tuple[str, str]] = {
    "UNSUPPORTED_FORMAT": ("This file type is not supported.", "Upload a recording in WAV, MP3, M4A, FLAC, OGG, OPUS, WebM or AAC format."),
    "EMPTY_FILE": ("The file is empty.", "Choose a recording that contains audio, then upload it again."),
    "UNREADABLE_AUDIO": ("The audio could not be read.", "The file may be damaged or in an unusual encoding. Re-export it as WAV or MP3 and try again."),
    "NO_AUDIO_STREAM": ("The file has no audio track.", "Upload a recording that contains sound (a video file needs an audio track)."),
    "SILENT_AUDIO": ("No sound was found in the recording.", "Check that the recording contains audible speech and is not muted."),
    "NO_SPEECH": ("No speech was detected in the recording.", "Check that the recording contains audible speech in English."),
    "TOO_SHORT": ("The recording is too short to process.", "Upload a recording that is at least a few seconds long."),
    "TOO_LONG": ("The recording is longer than this application can process.", "Split the recording into shorter parts and process them separately."),
    "FILE_TOO_LARGE": ("The file is too large.", "Upload a smaller file, or compress the recording."),
    "FILE_MISSING": ("The uploaded file is no longer available.", "Upload the recording again."),
    "API_KEY_MISSING": ("A service this step needs is not configured.", "Add the missing key to the .env file on the server, restart, and retry this step."),
    "API_KEY_INVALID": ("A service rejected its access key.", "Check the key in the .env file on the server, restart, and retry this step."),
    "API_RATE_LIMITED": ("The service is busy or its usage limit was reached.", "Wait a minute and retry this step. Work already finished is kept."),
    "API_UNAVAILABLE": ("The service could not be reached.", "Check the network connection and retry this step. Work already finished is kept."),
    "API_PAYLOAD_TOO_LARGE": ("The recording was too large for the transcription service.", "Upload a shorter or more compressed recording."),
    "API_ERROR": ("The service returned an error.", "Retry this step. If it keeps failing, the details are in Pipeline."),
    "LLM_BAD_RESPONSE": ("A processing step returned an unreadable answer.", "Retry this step. Work already finished is kept."),
    "BAD_RESPONSE": ("A processing step returned an unreadable answer.", "Retry this step. Work already finished is kept."),
    "ANALYSIS_FAILED": ("The meeting record could not be produced.", "Retry this step. The transcript is unaffected."),
    "TOO_LONG_FOR_BUDGET": ("This recording is too long for the current processing limit.", "Use a shorter recording, or ask the administrator to raise the limit."),
    "TOO_LONG_FOR_ONE_CALL": ("This recording is too long for the current processing limit.", "Use a shorter recording, or ask the administrator to raise the limit."),
    "TOKEN_BUDGET": ("Processing stopped at its size limit.", "Use a shorter recording, or ask the administrator to raise the limit."),
    "CALL_LIMIT": ("Processing stopped at its step limit.", "Retry this step, or ask the administrator to raise the limit."),
    "RUN_TIMEOUT": ("Processing took longer than the time limit.", "Retry this step. Long recordings may need a higher time limit."),
    "INTERRUPTED": ("Processing was interrupted.", "The server was restarted while this step was running. Retry this step; finished work is kept."),
    "JOB_NOT_FOUND": ("This meeting could not be found.", "Return to the meeting list and open it again."),
    "INTERNAL_ERROR": ("An unexpected problem stopped this step.", "Retry this step. If it happens again, the details are in Pipeline."),
}


def describe(err: PipelineError | Exception | dict | None) -> dict | None:
    """{code, title, message, action, detail} for any failure."""
    if err is None:
        return None
    if isinstance(err, dict):
        code, detail = err.get("code", "INTERNAL_ERROR"), err.get("detail") or err.get("message", "")
    elif isinstance(err, PipelineError):
        code, detail = err.code, err.message
    else:
        code, detail = "INTERNAL_ERROR", type(err).__name__
    title, action = CATALOG.get(code, CATALOG["INTERNAL_ERROR"])
    # A file or key problem is specific; its own message is already written for the user, so keep it as the explanation.
    message = detail if code in ("UNSUPPORTED_FORMAT", "TOO_SHORT", "TOO_LONG", "FILE_TOO_LARGE", "EMPTY_FILE", "UNREADABLE_AUDIO", "NO_AUDIO_STREAM", "SILENT_AUDIO", "NO_SPEECH") else title
    return {"code": code, "title": title, "message": message, "action": action, "detail": detail}
