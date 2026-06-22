import React from "react";
import termsHtml from "./terms.html?raw";
import { TermlyHtmlContent } from "./legalDocumentContent.jsx";

export default function TermsContent() {
  return <TermlyHtmlContent html={termsHtml} className="terms-notice-html" />;
}
