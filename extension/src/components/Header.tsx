import type { BackendStatus } from "../sidepanel/hooks";
import type { Language } from "../shared/types";

interface Props {
  status: BackendStatus;
  language: Language;
  onLanguageChange: (lang: Language) => void;
}

const STATUS_TEXT: Record<BackendStatus["kind"], string> = {
  checking: "Connecting…",
  ok: "Ready",
  no_key: "No API key",
  offline: "Backend offline",
};

export function Header({ status, language, onLanguageChange }: Props) {
  return (
    <header className="header">
      <div className="brand">
        <span className="brand-mark" aria-hidden="true">
          ✓
        </span>
        <span className="brand-name">YouVerify AI</span>
      </div>
      <div className="header-controls">
        <label className="visually-hidden" htmlFor="lang">
          Language
        </label>
        <select
          id="lang"
          className="lang-select"
          value={language}
          onChange={(e) => onLanguageChange(e.target.value as Language)}
          title="Language of the video and of explanations"
        >
          <option value="auto">Auto</option>
          <option value="en">EN</option>
          <option value="ru">RU</option>
        </select>
        <span className={`status-dot status-${status.kind}`} role="status" title={status.kind === "offline" ? status.message : STATUS_TEXT[status.kind]}>
          <span aria-hidden="true" className="dot" />
          {STATUS_TEXT[status.kind]}
        </span>
      </div>
    </header>
  );
}
