import React, { useEffect, useMemo, useRef } from "react";

const NUMBERED_SECTION_RE = /^\d+\.\s+[A-Z]/;

const FIELD_LIST_ITEMS = new Set([
  "names",
  "email addresses",
  "job titles",
  "passwords",
  "contact preferences",
  "billing addresses",
  "debit/credit card numbers",
  "phone numbers",
  "usernames",
  "mailing addresses",
  "contact or authentication data",
  "AI bots",
  "AI insights",
  "AI document generation",
  "AI research",
  "AI search",
  "AI translation",
  "Image analysis",
  "Video analysis",
  "Natural language processing",
]);

export function cleanSpacing(text) {
  return text
    .replace(/https:\s+\/\//gi, "https://")
    .replace(/http:\s+\/\//gi, "http://")
    .replace(/\s+([,.;:!?])/g, "$1")
    .replace(/\(\s+"/g, '("')
    .replace(/"\s+\)/g, '")')
    .replace(/"\s+([^"]+?)\s+"/g, '"$1"')
    .replace(/,"or"/gi, '," or "')
    .replace(/"\s+or\s+"/gi, '" or "')
    .replace(/\s{2,}/g, " ")
    .replace(/and\/\s+or/g, "and/or")
    .replace(/\(\s+(\d+)\s+\)/g, "($1)")
    .replace(/"([^"]+)"\(/g, '"$1" (')
    .replace(/\?\s+"/g, '?"')
    .trim();
}

function isMostlyUppercase(line) {
  const letters = line.replace(/[^a-zA-Z]/g, "");
  if (letters.length < 80) {
    return false;
  }
  const uppercaseLetters = (line.match(/[A-Z]/g) || []).length;
  return uppercaseLetters / letters.length >= 0.9;
}

function isLegalCapsBlock(line) {
  return line.length > 120 && isMostlyUppercase(line) && /[A-Z]{3,}/.test(line);
}

function isNumberedSection(line) {
  return NUMBERED_SECTION_RE.test(line) && line.length < 120;
}

function createStructuralChecker(config) {
  const skipTitles = new Set(config.skipTitles || []);
  const majorHeadings = new Set(config.majorHeadings || []);
  const subsectionHeadings = new Set(config.subsectionHeadings || []);

  return function isStructuralLine(line) {
    if (!line) {
      return true;
    }
    if (skipTitles.has(line)) {
      return true;
    }
    if (line.startsWith("Last updated")) {
      return true;
    }
    if (majorHeadings.has(line) || subsectionHeadings.has(line)) {
      return true;
    }
    if (isNumberedSection(line)) {
      return true;
    }
    if (config.isTocLine?.(line)) {
      return true;
    }
    return false;
  };
}

function defaultIsListItem(line) {
  if (!line || line.length > 260) {
    return false;
  }
  if (FIELD_LIST_ITEMS.has(line)) {
    return true;
  }
  if (/^[a-z][a-z /'-]*$/.test(line)) {
    return true;
  }
  if (/^To [A-Z]/.test(line) && line.includes(". ")) {
    return true;
  }
  if (/^If /.test(line) && line.length < 220) {
    return true;
  }
  if (/^(Right to |Depending upon|Log in to|Contact us using|Participation in|Receiving help|Facilitation in|Category [A-Z]-)/.test(line)) {
    return true;
  }
  if (/^(Business Transfers|Affiliates|Business Partners|Other Users|When we use Google Maps|Log and Usage Data|Device Data|Location Data)\./.test(line)) {
    return true;
  }
  return false;
}

function shouldMergeParagraphLine(line, previousLine, config, inProhibitedList, isListItem) {
  const isStructuralLine = createStructuralChecker(config);

  if (isStructuralLine(line) || isStructuralLine(previousLine)) {
    return false;
  }

  if (inProhibitedList && !line.startsWith("8.")) {
    return false;
  }

  if (isListItem(line) || isListItem(previousLine)) {
    return false;
  }

  if (NUMBERED_SECTION_RE.test(line)) {
    return false;
  }

  const previous = previousLine.trim();
  if (/[,;]$/.test(previous)) {
    return true;
  }

  if (!/[.!?:]$/.test(previous)) {
    return true;
  }

  return /^[a-z]/.test(line);
}

export function preprocessLegalText(text, config) {
  const isStructuralLine = createStructuralChecker(config);
  const isListItem = config.isListItem || defaultIsListItem;
  const lines = text.split("\n");
  const merged = [];
  let inProhibitedList = false;

  for (const rawLine of lines) {
    const line = rawLine.trim();
    if (!line) {
      if (merged.length && merged[merged.length - 1] !== "") {
        merged.push("");
      }
      continue;
    }

    if (config.listSection?.start && line.startsWith(config.listSection.start)) {
      inProhibitedList = true;
    } else if (config.listSection?.end && line.startsWith(config.listSection.end)) {
      inProhibitedList = false;
    }

    const previous = merged[merged.length - 1];
    if (
      previous &&
      previous !== "" &&
      shouldMergeParagraphLine(line, previous, config, inProhibitedList, isListItem)
    ) {
      merged[merged.length - 1] = `${previous} ${line}`;
      continue;
    }

    merged.push(line);
  }

  return merged
    .filter((line) => line !== "")
    .map((line) => cleanSpacing(line))
    .join("\n\n");
}

export function parseLegalBlocks(text, config) {
  const isStructuralLine = createStructuralChecker(config);
  const isListItem = config.isListItem || defaultIsListItem;
  const listTriggers = config.listTriggers || [];
  const lines = text.split("\n");
  const blocks = [];
  let listItems = [];
  let listMode = null;

  const flushList = () => {
    if (listItems.length) {
      blocks.push({ type: "ul", items: [...listItems] });
      listItems = [];
    }
    listMode = null;
  };

  const matchesListTrigger = (line) =>
    listTriggers.some((trigger) => line.endsWith(trigger));

  for (const rawLine of lines) {
    const line = rawLine.trim();
    if (!line) {
      flushList();
      continue;
    }

    if (config.skipTitles?.includes(line)) {
      continue;
    }

    if (line.startsWith("Last updated")) {
      flushList();
      blocks.push({ type: "subtitle", text: line });
      continue;
    }

    if (config.majorHeadings?.includes(line) || isNumberedSection(line)) {
      flushList();
      blocks.push({ type: "h2", text: line });
      if (config.listSection?.start && line.startsWith(config.listSection.start)) {
        listMode = "prohibited";
      }
      continue;
    }

    if (config.subsectionHeadings?.includes(line)) {
      flushList();
      blocks.push({ type: "h3", text: line });
      continue;
    }

    if (config.isTocLine?.(line)) {
      flushList();
      const items = line.match(/\d+\.\s+[^0-9]+?(?=\s*\d+\.|$)/g) || [];
      blocks.push({ type: "toc", items: items.map((item) => item.trim()) });
      continue;
    }

    if (listMode === "prohibited" && !line.startsWith(config.listSection?.end || "ZZZ")) {
      listItems.push(line);
      continue;
    }

    if (matchesListTrigger(line)) {
      flushList();
      blocks.push({ type: "p", text: line });
      listMode = "triggered";
      continue;
    }

    if (listMode === "triggered" && isListItem(line)) {
      listItems.push(line);
      continue;
    }

    if (listMode === "triggered" && !isStructuralLine(line) && line.length < 220) {
      listItems.push(line);
      continue;
    }

    if (listMode === "triggered") {
      flushList();
    }

    if (config.inlineListTriggers?.some((trigger) => line.endsWith(trigger))) {
      flushList();
      blocks.push({ type: "p", text: line });
      listMode = "inline";
      continue;
    }

    if (
      listMode === "inline" &&
      line.length < 220 &&
      !isNumberedSection(line) &&
      !config.subsectionHeadings?.includes(line)
    ) {
      listItems.push(line);
      continue;
    }

    if (isLegalCapsBlock(line)) {
      flushList();
      const previous = blocks[blocks.length - 1];
      if (previous?.type === "caps") {
        previous.text = `${previous.text} ${line}`;
      } else {
        blocks.push({ type: "caps", text: line });
      }
      continue;
    }

    flushList();
    blocks.push({ type: "p", text: line });
  }

  flushList();
  return blocks;
}

function formatInlineText(text, keyPrefix) {
  const regex = /(https?:\/\/[^\s,]+|support@automatisor\.com)/g;
  const parts = [];
  let last = 0;
  let partIndex = 0;

  const pushSegment = (segment) => {
    if (!segment) {
      return;
    }

    const sentenceRegex = /[A-Z][A-Z\s,'()\-&]{24,}[.!]/g;
    let sentenceLast = 0;
    let sentenceIndex = 0;
    let matchedSentence = false;

    for (const match of segment.matchAll(sentenceRegex)) {
      const sentence = match[0];
      const lowercaseCount = (sentence.match(/[a-z]/g) || []).length;
      if (lowercaseCount > 0 || sentence.replace(/[^a-zA-Z]/g, "").length < 25) {
        continue;
      }

      matchedSentence = true;
      const start = match.index ?? 0;
      if (start > sentenceLast) {
        parts.push(segment.slice(sentenceLast, start));
      }

      const key = `${keyPrefix}-caps-${partIndex}-${sentenceIndex}`;
      partIndex += 1;
      sentenceIndex += 1;
      parts.push(<strong key={key}>{sentence}</strong>);
      sentenceLast = start + sentence.length;
    }

    if (matchedSentence) {
      if (sentenceLast < segment.length) {
        parts.push(segment.slice(sentenceLast));
      }
      return;
    }

    parts.push(segment);
  };

  for (const match of text.matchAll(regex)) {
    const start = match.index ?? 0;
    if (start > last) {
      pushSegment(text.slice(last, start));
    }

    const value = match[0];
    const key = `${keyPrefix}-${partIndex}`;
    partIndex += 1;

    if (value.startsWith("http")) {
      parts.push(
        <a key={key} href={value} target="_blank" rel="noopener noreferrer">
          {value}
        </a>
      );
    } else if (value.includes("@")) {
      parts.push(
        <a key={key} href={`mailto:${value}`}>
          {value}
        </a>
      );
    }

    last = start + value.length;
  }

  if (last < text.length) {
    pushSegment(text.slice(last));
  }

  if (parts.length === 0) {
    return text;
  }

  if (parts.length === 1 && typeof parts[0] === "string") {
    return parts[0];
  }

  return <>{parts}</>;
}

function formatParagraph(text, keyPrefix) {
  const labelMatch = text.match(/^([A-Za-z][A-Za-z\s]+:)\s*(.*)$/s);
  const label = labelMatch?.[1] ?? "";
  const looksLikeUrlFragment = /https?|www\.|\/\//i.test(label);

  if (labelMatch && label.length < 40 && !looksLikeUrlFragment) {
    const [, , rest] = labelMatch;
    return (
      <>
        <strong>{label}</strong> {rest ? formatInlineText(rest, `${keyPrefix}-body`) : null}
      </>
    );
  }

  return formatInlineText(text, keyPrefix);
}

export function LegalDocumentContent({ blocks }) {
  return (
    <div className="terms-content">
      {blocks.map((block, index) => {
        switch (block.type) {
          case "subtitle":
            return (
              <p key={index} className="terms-subtitle">
                <strong>Last updated</strong> {block.text.replace(/^Last updated\s*/i, "")}
              </p>
            );
          case "h2":
            return (
              <h4 key={index} className="terms-section-heading">
                {block.text}
              </h4>
            );
          case "h3":
            return (
              <h5 key={index} className="terms-subsection-heading">
                {block.text}
              </h5>
            );
          case "toc":
            return (
              <ol key={index} className="terms-toc">
                {block.items.map((item) => (
                  <li key={item}>{item}</li>
                ))}
              </ol>
            );
          case "ul":
            return (
              <ul key={index} className="terms-list">
                {block.items.map((item) => (
                  <li key={item}>{formatParagraph(item, `li-${index}-${item.slice(0, 12)}`)}</li>
                ))}
              </ul>
            );
          case "caps":
            return (
              <p key={index} className="terms-caps">
                <strong>{block.text}</strong>
              </p>
            );
          default:
            return <p key={index}>{formatParagraph(block.text, `p-${index}`)}</p>;
        }
      })}
    </div>
  );
}

export function buildLegalDocument(text, config) {
  const preprocessed = preprocessLegalText(text, config);
  return parseLegalBlocks(preprocessed, config);
}

export function parseTermlyHtml(html) {
  const styleBlocks = [...html.matchAll(/<style>([\s\S]*?)<\/style>/gi)].map(
    (match) => match[1].trim()
  );
  const bodyMatch = html.match(/<div data-custom-class="body">([\s\S]*)/i);
  let markup = bodyMatch?.[1]?.trim() ?? html.trim();

  const trailingStyleIndex = markup.lastIndexOf("<style>");
  if (trailingStyleIndex !== -1) {
    markup = markup.slice(0, trailingStyleIndex).trim();
  }

  markup = markup.replace(/\s*<\/div>\s*$/i, "").trim();

  // Termly spacer divs (br-only) are inconsistent — remove them and use CSS spacing.
  markup = markup.replace(/<div(?:\s[^>]*)?>\s*<br\s*\/?>\s*<\/div>/gi, "");

  markup = boldPostContactAddress(markup);

  return {
    styles: styleBlocks.join("\n"),
    markup,
  };
}

function boldPostContactAddress(markup) {
  const startToken = "contact us by post at:";
  const endToken = 'id="request"';
  const start = markup.indexOf(startToken);
  const end = markup.indexOf(endToken, start);
  if (start === -1 || end === -1) {
    return markup;
  }

  const prefix = markup.slice(0, start);
  let section = markup.slice(start, end);
  const suffix = markup.slice(end);

  section = section.replace(
    /<bdt class="question([^"]*)">([\s\S]*?)<\/bdt>/gi,
    (match, attrs, inner) => {
      if (/<strong>/i.test(inner)) {
        return match;
      }

      const tagIndex = inner.indexOf("<");
      if (tagIndex === -1) {
        return `<strong><bdt class="question${attrs}">${inner}</bdt></strong>`;
      }

      const text = inner.slice(0, tagIndex);
      const rest = inner.slice(tagIndex);
      return `<strong><bdt class="question${attrs}">${text}</bdt></strong>${rest}`;
    }
  );

  return prefix + section + suffix;
}

export function TermlyHtmlContent({ html, className = "termly-embed" }) {
  const containerRef = useRef(null);
  const { styles, markup } = useMemo(() => parseTermlyHtml(html), [html]);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) {
      return undefined;
    }

    const handleAnchorClick = (event) => {
      const anchor = event.target.closest('a[href^="#"]');
      if (!anchor || !container.contains(anchor)) {
        return;
      }

      const targetId = decodeURIComponent(anchor.getAttribute("href").slice(1));
      if (!targetId) {
        return;
      }

      const target =
        container.querySelector(`#${CSS.escape(targetId)}`) ||
        container.querySelector(`[name="${targetId}"]`);

      if (!target) {
        return;
      }

      event.preventDefault();
      target.scrollIntoView({ behavior: "smooth", block: "start" });
    };

    container.addEventListener("click", handleAnchorClick);
    return () => container.removeEventListener("click", handleAnchorClick);
  }, [markup]);

  return (
    <div ref={containerRef} className={className}>
      {styles ? <style>{styles}</style> : null}
      <div data-custom-class="body" dangerouslySetInnerHTML={{ __html: markup }} />
    </div>
  );
}
