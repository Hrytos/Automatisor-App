import React from "react";
import privacyHtml from "./privacy.html?raw";
import { TermlyHtmlContent } from "./legalDocumentContent.jsx";

export default function PrivacyContent() {
  return <TermlyHtmlContent html={privacyHtml} className="privacy-notice-html" />;
}
