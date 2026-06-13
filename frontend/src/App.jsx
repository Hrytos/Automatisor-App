import React, {
  forwardRef,
  useEffect,
  useImperativeHandle,
  useMemo,
  useRef,
  useState,
} from "react";
import {
  Link,
  Outlet,
  useOutletContext,
  useLocation,
  Navigate,
  Route,
  Routes,
  useNavigate,
  useSearchParams,
} from "react-router-dom";

import brWilliamsSampleReport from "../../backend/sample-report/data_structure.json";
import reportStructure from "./report_section_structure.json";
import CreditsPage from "./CreditsPage.jsx";
import BillingPage from "./BillingPage.jsx";
import ShareReportDialog from "./ShareReportDialog.jsx";
import { isFreeEmail } from "free-email-domains-list";

const SESSION_KEY = "automatisor_auth_workspace_v2";
const REPORT_CONTEXT_KEY = "automatisor_selected_report_v1";
const PRE_ASSESSMENT_CONTEXT_KEY = "automatisor_selected_pre_assessment_v1";
const REPORT_CONFIDENCE_FILTERS = ["All", "High"];
const REPORT_RATING_FIELDS = [
  {
    key: "coverage",
    label: "Coverage",
    question: "Did you get the information you were looking for?",
  },
  {
    key: "accuracy",
    label: "Accuracy",
    question: "Is the information accurate according to you?",
  },
  {
    key: "value",
    label: "Value",
    question: "Is this information valuable to you?",
  },
];
const REPORT_RATING_VALUES = [1, 2, 3, 4, 5];
const REPORT_NOT_FOUND_VALUES = new Set([
  "n/a",
  "na",
  "none",
  "none found",
  "not available",
  "not found",
  "not identified",
  "no information found",
  "unknown",
]);
const REPORT_NO_CONFIDENCE_PATHS = new Set([
  "part_1_account_identification.account_identity.target_facility_addresses",
  "part_1_account_identification.facility_profile.full_address",
]);
const DEFAULT_SITE_ADDRESS =
  "366, Remington Boulevard, Bolingbrook, Will County, Illinois, 60440, United States";
const DEFAULT_SITE_COORDS = {
  lat: 41.6816291809082,
  lng: -88.0822601318359,
};

let frontendConfigPromise = null;
let googleMapsPromise = null;

function writeAutocompleteDebug(label, payload = {}) {
  const entry = {
    label,
    payload,
    at: new Date().toISOString(),
  };
  window.__automatisorAutocompleteDebug = window.__automatisorAutocompleteDebug || [];
  window.__automatisorAutocompleteDebug.push(entry);
  console.debug(`[AutomatiSOR autocomplete] ${label}`, payload);
}

function loadSession() {
  try {
    const raw = window.sessionStorage.getItem(SESSION_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

function saveSession(nextState) {
  try {
    window.sessionStorage.setItem(SESSION_KEY, JSON.stringify(nextState));
    window.dispatchEvent(new CustomEvent("sessionUpdated", { detail: nextState }));
  } catch {
    // Ignore.
  }
}

function loadReportContext() {
  try {
    const raw = window.sessionStorage.getItem(REPORT_CONTEXT_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

function saveReportContext(nextState) {
  try {
    window.sessionStorage.setItem(REPORT_CONTEXT_KEY, JSON.stringify(nextState));
  } catch {
    // Ignore.
  }
}

function loadPreAssessmentContext() {
  try {
    const raw = window.sessionStorage.getItem(PRE_ASSESSMENT_CONTEXT_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

function savePreAssessmentContext(nextState) {
  try {
    window.sessionStorage.setItem(PRE_ASSESSMENT_CONTEXT_KEY, JSON.stringify(nextState));
  } catch {
    // Ignore.
  }
}

function clearPreAssessmentContext() {
  try {
    window.sessionStorage.removeItem(PRE_ASSESSMENT_CONTEXT_KEY);
  } catch {
    // Ignore.
  }
}

function hasReportMetadata(reportMetadata) {
  return Boolean(
    reportMetadata &&
      typeof reportMetadata === "object" &&
      !Array.isArray(reportMetadata) &&
      Object.keys(reportMetadata).length,
  );
}

function buildSessionFromPayload(session, payload) {
  return {
    ...session,
    email: payload.email || session?.email || "",
    userMode: payload.user_mode || session?.userMode || "existing_user",
    nextStep: payload.next_step || "workspace",
    authVerified: payload.next_step === "workspace" ? true : session?.authVerified || false,
    customerId: payload.customer_id || null,
    accountId: payload.account_id || null,
    activeAccountId: payload.active_account_id || payload.account_id || null,
    companyName: payload.company_name || "",
    companyDomain: payload.company_domain || "",
    creditsUsedTotal: Number(payload.credits_used_total || 0),
    creditsUsedThisMonth: Number(payload.credits_used_this_month || 0),
    sites: Array.isArray(payload.sites) ? payload.sites : session?.sites || [],
    wishlist: Array.isArray(payload.wishlist) ? payload.wishlist : session?.wishlist || [],
    accounts: Array.isArray(payload.accounts) ? payload.accounts : session?.accounts || [],
    preAssessmentPriceCredits: Number(payload.pre_assessment_price_credits || session?.preAssessmentPriceCredits || 2),
  };
}

function formatDateTime(value) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return new Intl.DateTimeFormat(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  }).format(date);
}

function formatReportDate(value) {
  const match = String(value || "").match(/^(\d{4})-(\d{2})-(\d{2})/);
  if (!match) return "";
  const [, year, month, day] = match;
  const monthNumber = Number(month);
  const dayNumber = Number(day);
  if (monthNumber < 1 || monthNumber > 12 || dayNumber < 1 || dayNumber > 31) return "";
  const monthName = new Intl.DateTimeFormat("en-US", { month: "long" }).format(
    new Date(Date.UTC(Number(year), monthNumber - 1, 1)),
  );
  return `${dayNumber} ${monthName} ${year}`;
}

function renderReportValue(value) {
  if (value === null || value === undefined || value === "") {
    return <span className="report-empty-value">Not available</span>;
  }
  if (Array.isArray(value) || typeof value === "object") {
    return (
      <pre className="report-json-block">{JSON.stringify(value, null, 2)}</pre>
    );
  }
  if (typeof value === "boolean") {
    return value ? "Yes" : "No";
  }
  return String(value);
}

function normalizeReportRatingMetadata(raw) {
  const source = raw && typeof raw === "object" && !Array.isArray(raw) ? raw : {};
  const normalizeRating = (value) => {
    const nextValue = Number(value);
    return Number.isInteger(nextValue) && nextValue >= 1 && nextValue <= 5 ? nextValue : null;
  };
  return {
    coverage: normalizeRating(source.coverage),
    accuracy: normalizeRating(source.accuracy),
    value: normalizeRating(source.value),
    additional_feedback: String(source.additional_feedback || ""),
    updated_at: source.updated_at || "",
  };
}

function normalizeRecommendations(raw) {
  const source = raw && typeof raw === "object" && !Array.isArray(raw) ? raw : {};
  return {
    status: String(source.status || ""),
    company_sites: Array.isArray(source.company_sites) ? source.company_sites : [],
    nearby_sites: Array.isArray(source.nearby_sites) ? source.nearby_sites : [],
    error: String(source.error || ""),
  };
}

function recommendationText(value) {
  return String(value || "").trim();
}

function recommendationTitle(recommendation) {
  return (
    recommendationText(recommendation.site_name) ||
    recommendationText(recommendation.company_name) ||
    recommendationText(recommendation.google_place_name)
  );
}

function recommendationAddress(recommendation) {
  return (
    recommendationText(recommendation.site_address) ||
    recommendationText(recommendation.google_formatted_address) ||
    recommendationText(recommendation.full_address)
  );
}

function recommendationMapsUrl(recommendation) {
  return recommendationText(recommendation.google_maps_uri) || recommendationText(recommendation.source_url);
}

function hasHydratedRecommendationDetails(recommendation) {
  return Boolean(
    recommendationTitle(recommendation) ||
      recommendationAddress(recommendation) ||
      recommendationText(recommendation.google_maps_uri) ||
      recommendationText(recommendation.source_url),
  );
}

function recommendationAddFacilityDraft(recommendation, fallbackCompanyName = "") {
  const companyName =
    recommendationText(recommendation.company_name) ||
    recommendationText(recommendation.site_name) ||
    fallbackCompanyName;
  const website =
    recommendationText(recommendation.website) ||
    recommendationText(recommendation.company_domain) ||
    recommendationText(recommendation.account_domain) ||
    recommendationText(recommendation.source_url);
  const domain = normalizeCandidateDomain({ website });
  const address = recommendationAddress(recommendation);
  return {
    org_name: companyName,
    company_name: companyName,
    org_domain: domain,
    full_address: address,
    street: recommendationText(recommendation.site_street) || recommendationText(recommendation.street),
    city: recommendationText(recommendation.site_city) || recommendationText(recommendation.city),
    state: recommendationText(recommendation.site_state) || recommendationText(recommendation.state),
    zip: recommendationText(recommendation.site_zip) || recommendationText(recommendation.zip_code) || recommendationText(recommendation.zip),
    country: recommendationText(recommendation.country) || "US",
    place_id: recommendationText(recommendation.place_id),
    request_basis: "",
    selected_candidate: null,
    justification: "",
  };
}

function wishlistItemTitle(item) {
  return recommendationText(item?.company_name) || recommendationText(item?.site_name) || "Wishlisted site";
}

function wishlistItemAddress(item) {
  return recommendationText(item?.full_address) || recommendationText(item?.site_address) || recommendationText(item?.google_formatted_address);
}

function wishlistItemMapsUrl(item) {
  const metadata = item?.metadata && typeof item.metadata === "object" && !Array.isArray(item.metadata) ? item.metadata : {};
  return (
    recommendationText(item?.google_maps_uri) ||
    recommendationText(item?.source_url) ||
    recommendationText(metadata.google_maps_uri) ||
    recommendationText(metadata.source_url)
  );
}

function isWrappedReportField(value) {
  return (
    value &&
    typeof value === "object" &&
    !Array.isArray(value) &&
    "value" in value &&
    ("fetch_confidence" in value ||
      "confidence_score" in value ||
      "description" in value ||
      isSourceVariantReportValue(value.value) ||
      isSourceVariantReportValue(value.description))
  );
}

function isSourceVariantReportValue(value) {
  return (
    value &&
    typeof value === "object" &&
    !Array.isArray(value) &&
    ("high" in value || "all" in value)
  );
}

function reportLabelFromKey(key) {
  return String(key || "")
    .replace(/_/g, " ")
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function isMissingReportValue(value) {
  if (value === null || value === undefined || value === "") return true;
  if (Array.isArray(value)) return value.length === 0 || value.every(isMissingReportValue);
  if (typeof value !== "string") return false;
  return REPORT_NOT_FOUND_VALUES.has(value.trim().toLowerCase());
}

function confidenceBand(score) {
  const numericScore = Number(score);
  if (!Number.isFinite(numericScore)) return "Low";
  if (numericScore >= 0.8) return "High";
  if (numericScore >= 0.4) return "Medium";
  return "Low";
}

function reportConfidenceLabel(fetchConfidence, confidenceScore) {
  const signalBand = confidenceBand(fetchConfidence);
  const validationBand = confidenceBand(confidenceScore);

  if (signalBand === "Low" || validationBand === "Low") return "Low";
  if (signalBand === "High" && validationBand === "High") return "High";
  return "Medium";
}

function shouldHideReportConfidence(path) {
  return (
    path === "account_snapshot" ||
    String(path || "").startsWith("account_snapshot.") ||
    REPORT_NO_CONFIDENCE_PATHS.has(path)
  );
}

function shouldPersistReportAcrossConfidenceFilters(path) {
  return shouldHideReportConfidence(path);
}

function formatStructuredReportValue(value) {
  if (Array.isArray(value)) {
    return value
      .map((item) => formatStructuredReportValue(item))
      .filter(Boolean)
      .join(", ");
  }

  if (value && typeof value === "object") {
    return Object.entries(value)
      .filter(([, nestedValue]) => !isMissingReportValue(nestedValue))
      .map(([key, nestedValue]) => `${reportLabelFromKey(key)}: ${formatStructuredReportValue(nestedValue)}`)
      .join("; ");
  }

  return String(value ?? "");
}

function getReportValueByPath(data, path) {
  if (!path) return data;
  return String(path)
    .split(".")
    .reduce((current, part) => current?.[part], data);
}

function unwrapReportField(data) {
  if (isWrappedReportField(data)) {
    return {
      value: data.value,
      description: data.description,
      confidence: reportConfidenceLabel(data.fetch_confidence, data.confidence_score),
    };
  }

  return {
    value: data,
    description: "",
    confidence: reportConfidenceLabel(),
  };
}

function sourceReportValueVariants(value, description) {
  if (isSourceVariantReportValue(value) || isSourceVariantReportValue(description)) {
    const high = isMissingReportValue(value?.high) ? "" : formatStructuredReportValue(value.high);
    const all = isMissingReportValue(value?.all) ? "" : formatStructuredReportValue(value.all);
    const highDescription = isMissingReportValue(description?.high) ? "" : formatStructuredReportValue(description.high);
    const allDescription = isMissingReportValue(description?.all) ? "" : formatStructuredReportValue(description.all);
    return {
      high,
      all,
      highDescription,
      allDescription,
      highConfidence: high || highDescription ? "High" : "",
      allConfidence: all || allDescription ? "Medium" : high || highDescription ? "High" : "",
    };
  }
  return null;
}

function sourceReportItemForFilter(item, activeFilter) {
  if (!item.variants) return item;

  const variant =
    activeFilter === "High"
      ? "high"
      : item.variants.all || item.variants.allDescription
        ? "all"
        : "high";
  const value = item.variants[variant];
  const description = item.variants[`${variant}Description`];
  if (isMissingReportValue(value) && isMissingReportValue(description)) return null;

  return {
    ...item,
    value,
    description,
    confidence: variant === "high" ? item.variants.highConfidence : item.variants.allConfidence,
  };
}

function flattenStructuredReportRows(data, prefix = [], inheritedConfidence = {}) {
  if (isWrappedReportField(data)) {
    if (isMissingReportValue(data.value) && isMissingReportValue(data.description)) return [];
    const id = prefix.join(".");
    const variants = sourceReportValueVariants(data.value, data.description);
    return [
      {
        id,
        field: prefix.map(reportLabelFromKey).join(" / "),
        value: variants ? variants.all || variants.high : formatStructuredReportValue(data.value),
        description: variants
          ? variants.allDescription || variants.highDescription
          : isMissingReportValue(data.description)
            ? ""
            : formatStructuredReportValue(data.description),
        confidence: variants
          ? variants.allConfidence || variants.highConfidence
          : reportConfidenceLabel(
              data.fetch_confidence ?? inheritedConfidence.fetch_confidence,
              data.confidence_score ?? inheritedConfidence.confidence_score,
            ),
        variants,
      },
    ];
  }

  if (isMissingReportValue(data)) return [];

  if (Array.isArray(data)) {
    if (data.every((item) => !item || typeof item !== "object" || Array.isArray(item))) {
      const value = formatStructuredReportValue(data.filter((item) => !isMissingReportValue(item)));
      if (!value) return [];
      return [
        {
          id: prefix.join("."),
          field: prefix.map(reportLabelFromKey).join(" / "),
          value,
          description: "",
          confidence: reportConfidenceLabel(
            inheritedConfidence.fetch_confidence,
            inheritedConfidence.confidence_score,
          ),
        },
      ];
    }

    return data.flatMap((item, index) =>
      flattenStructuredReportRows(item, [...prefix, `${index + 1}`], inheritedConfidence),
    );
  }

  if (data && typeof data === "object") {
    return Object.entries(data).flatMap(([key, value]) =>
      flattenStructuredReportRows(value, [...prefix, key], inheritedConfidence),
    );
  }

  const id = prefix.join(".");
  return [
    {
      id,
      field: prefix.map(reportLabelFromKey).join(" / "),
      value: formatStructuredReportValue(data),
      description: "",
      confidence: reportConfidenceLabel(
        inheritedConfidence.fetch_confidence,
        inheritedConfidence.confidence_score,
      ),
    },
  ];
}

function structuredReportRowsFromConfig(reportData, table) {
  const tableData = getReportValueByPath(reportData, table.data_path);
  const tableHideConfidence = Boolean(table.hide_confidence);

  return (table.rows || []).flatMap((rowConfig) => {
    const rowData = getReportValueByPath(tableData, rowConfig.field);
    const fullPath = `${table.data_path}.${rowConfig.field}`;
    const generatedLabel = reportLabelFromKey(rowConfig.field);
    const configuredLabel = rowConfig.label || generatedLabel;

    return flattenStructuredReportRows(rowData, [generatedLabel]).map((row, index) => {
      const hideConfidence = tableHideConfidence || shouldHideReportConfidence(fullPath);
      return {
        ...row,
        id: `${table.data_path}.${rowConfig.field}.${index}`,
        confidence: hideConfidence ? null : row.confidence,
        hideConfidence,
        persistAcrossFilters: shouldPersistReportAcrossConfidenceFilters(fullPath),
        field:
          row.field === generatedLabel
            ? configuredLabel
            : row.field.startsWith(`${generatedLabel} / `)
              ? `${configuredLabel}${row.field.slice(generatedLabel.length)}`
              : row.field,
      };
    });
  });
}

function structuredReportRecordsFromConfig(reportData, table) {
  const records = getReportValueByPath(reportData, table.data_path);
  if (!Array.isArray(records)) return [];
  const hideConfidence = Boolean(table.hide_confidence);

  return records
    .map((record, recordIndex) => {
      const cells = (table.columns || []).map((column) => {
        const cell = unwrapReportField(getReportValueByPath(record, column.field));
        return {
          id: column.field,
          label: column.label,
          value: isMissingReportValue(cell.value) ? "" : formatStructuredReportValue(cell.value),
          description: isMissingReportValue(cell.description) ? "" : formatStructuredReportValue(cell.description),
          confidence: hideConfidence ? null : cell.confidence,
        };
      });

      if (cells.every((cell) => !cell.value)) return null;

      const confidence = hideConfidence
        ? null
        : (() => {
            const visibleConfidences = cells
              .filter((cell) => cell.value)
              .map((cell) => cell.confidence);
            if (visibleConfidences.includes("Low")) return "Low";
            if (visibleConfidences.includes("Medium")) return "Medium";
            return "High";
          })();

      return {
        id: `${table.data_path}.${recordIndex}`,
        cells,
        confidence,
        hideConfidence,
      };
    })
    .filter(Boolean);
}

function structuredOperationalSnapshotRowsFromConfig(reportData, table) {
  const tableData = getReportValueByPath(reportData, table.data_path);
  const hideConfidence = Boolean(table.hide_confidence);

  return (table.rows || [])
    .map((rowConfig) => {
      const operationData = getReportValueByPath(tableData, rowConfig.field);
      const nature = unwrapReportField(operationData?.nature);
      const automation = unwrapReportField(operationData?.automation);
      const natureVariants = sourceReportValueVariants(nature.value, nature.description);
      const automationVariants = sourceReportValueVariants(automation.value, automation.description);
      const natureValue = natureVariants
        ? natureVariants.all || natureVariants.high
        : isMissingReportValue(nature.value)
          ? ""
          : formatStructuredReportValue(nature.value);
      const natureDescription = natureVariants
        ? natureVariants.allDescription || natureVariants.highDescription
        : isMissingReportValue(nature.description)
          ? ""
          : formatStructuredReportValue(nature.description);
      const automationValue = automationVariants
        ? automationVariants.all || automationVariants.high
        : isMissingReportValue(automation.value)
          ? ""
          : formatStructuredReportValue(automation.value);
      const automationDescription = automationVariants
        ? automationVariants.allDescription || automationVariants.highDescription
        : isMissingReportValue(automation.description)
          ? ""
          : formatStructuredReportValue(automation.description);

      if (!natureValue && !natureDescription && !automationValue && !automationDescription) return null;

      return {
        id: `${table.data_path}.${rowConfig.field}`,
        operation: rowConfig.label || reportLabelFromKey(rowConfig.field),
        fields: [
          {
            id: "nature",
            label: "Nature Of Operation",
            value: natureValue,
            description: natureDescription,
            confidence: hideConfidence ? null : nature.confidence,
            variants: natureVariants,
          },
          {
            id: "automation",
            label: "Automation",
            value: automationValue,
            description: automationDescription,
            confidence: hideConfidence ? null : automation.confidence,
            variants: automationVariants,
          },
        ].filter((field) => field.value || field.description),
        hideConfidence,
      };
    })
    .filter(Boolean);
}

function structuredReportItemFromConfig(reportData, config, id, number, description) {
  if (config.tables?.length) {
    const children = config.tables.map((table, index) =>
      structuredReportItemFromConfig(
        reportData,
        table,
        `${id}.${table.id || table.title || index}`.replace(/\s+/g, "_").toLowerCase(),
        `${number}.${index + 1}`,
        "",
      ),
    );
    return {
      id,
      number,
      title: config.title || config.section_title,
      description,
      tableType: "group",
      hideConfidence: true,
      columns: [],
      rows: [],
      children,
    };
  }

  return {
    id,
    number,
    title: config.title || config.section_title,
    description,
    tableType: config.table_type || "key_value",
    hideConfidence: Boolean(config.hide_confidence),
    columns: config.columns || [],
    rows:
      config.table_type === "records"
        ? structuredReportRecordsFromConfig(reportData, config)
        : config.table_type === "operational_snapshot"
          ? structuredOperationalSnapshotRowsFromConfig(reportData, config)
        : structuredReportRowsFromConfig(reportData, config),
  };
}

function filterStructuredReportItem(item, activeFilter, keepUnfiltered) {
  if (item.tableType === "group") {
    const children = (item.children || [])
      .map((child) => filterStructuredReportItem(child, activeFilter, keepUnfiltered))
      .filter((child) => child.rows.length > 0 || (child.children && child.children.length > 0));
    return {
      ...item,
      rows: [],
      children,
    };
  }

  if (item.tableType === "operational_snapshot") {
    return {
      ...item,
      rows: item.rows
        .map((row) => ({
          ...row,
          fields: (row.fields || [])
            .map((field) => sourceReportItemForFilter(field, activeFilter))
            .filter(
              (field) =>
                field &&
                (field.variants ||
                  row.hideConfidence ||
                  (field.confidence !== "Low" &&
                    (activeFilter === "All" || field.confidence === activeFilter))),
            ),
        }))
        .filter((row) => row.fields.length > 0),
    };
  }

  return {
    ...item,
    rows:
      activeFilter === "All" || keepUnfiltered
        ? item.rows
            .map((row) => sourceReportItemForFilter(row, activeFilter))
            .filter((row) => row && (row.variants || row.hideConfidence || row.confidence !== "Low"))
        : item.rows
            .map((row) => sourceReportItemForFilter(row, activeFilter))
            .filter(
              (row) =>
                row &&
                (row.persistAcrossFilters ||
                !row.hideConfidence &&
                row.confidence === activeFilter &&
                row.confidence !== "Low"),
            ),
  };
}

function structuredItemRowCount(item) {
  if (item.tableType === "group") {
    return (item.children || []).reduce((total, child) => total + structuredItemRowCount(child), 0);
  }
  return item.rows.length;
}

function collectStructuredConfidenceCounts(item, counts) {
  if (item.tableType === "group") {
    (item.children || []).forEach((child) => collectStructuredConfidenceCounts(child, counts));
    return counts;
  }

  item.rows.forEach((row) => {
    if (item.tableType === "operational_snapshot") {
      (row.fields || []).forEach((field) => {
        if (field.variants) {
          if ((field.variants.high || field.variants.highDescription) && counts.High !== undefined) {
            counts.High += 1;
          }
        } else if (
          !row.hideConfidence &&
          field.confidence &&
          field.confidence !== "Low" &&
          counts[field.confidence] !== undefined
        ) {
          counts[field.confidence] += 1;
        }
      });
    } else if (row.variants) {
      if ((row.variants.high || row.variants.highDescription) && counts.High !== undefined) {
        counts.High += 1;
      }
    } else if (
      !row.hideConfidence &&
      row.confidence &&
      row.confidence !== "Low" &&
      counts[row.confidence] !== undefined
    ) {
      counts[row.confidence] += 1;
    }
  });
  return counts;
}

function makeStructuredReportSections(reportData) {
  return (reportStructure.sections || []).map((section, sectionIndex) => {
    const sectionNumber = String(sectionIndex + 1);
    let items = [];

    if (section.tables?.length) {
      items = section.tables.map((table, itemIndex) =>
        structuredReportItemFromConfig(
          reportData,
          table,
          `${section.section_id}.${table.data_path}.${table.title}`
            .replace(/\s+/g, "_")
            .toLowerCase(),
          `${sectionNumber}.${itemIndex + 1}`,
          "",
        ),
      );
    } else if (section.subsections?.length) {
      items = section.subsections.map((subsection, itemIndex) =>
        structuredReportItemFromConfig(
          reportData,
          subsection,
          `${section.section_id}.${subsection.id}`,
          `${sectionNumber}.${itemIndex + 1}`,
          "",
        ),
      );
    } else {
      const fallbackConfig = {
        title: section.section_title,
        data_path: section.section_id,
        rows: Object.keys(reportData[section.section_id] || {}).map((field) => ({
          field,
          label: reportLabelFromKey(field),
        })),
      };
      items = [
        structuredReportItemFromConfig(
          reportData,
          fallbackConfig,
          section.section_id,
          `${sectionNumber}.1`,
          "",
        ),
      ];
    }

    return {
      id: section.section_id,
      number: sectionNumber,
      title: section.section_title,
      description: section.section_description,
      items,
    };
  });
}

function ReportConfidenceBadge({ label }) {
  if (!label) return null;
  return (
    <span className={`structured-report-confidence structured-report-confidence-${label}`}>
      {label}
    </span>
  );
}

function StructuredReportKeyValueTable({ rows, hideConfidenceColumn }) {
  const showConfidenceColumn = !hideConfidenceColumn && rows.some((row) => !row.hideConfidence);

  return (
    <div className="structured-report-table-wrap">
      <table className="structured-report-table structured-report-key-value-table">
        <thead>
          <tr>
            <th>Field</th>
            <th>Value</th>
            <th>Description</th>
            {showConfidenceColumn && <th>Confidence</th>}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.id}>
              <td data-label="Field">{row.field}</td>
              <td data-label="Value">{row.value}</td>
              <td data-label="Description">{row.description}</td>
              {showConfidenceColumn && (
                <td data-label="Confidence">
                  {!row.hideConfidence && <ReportConfidenceBadge label={row.confidence} />}
                </td>
              )}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function StructuredReportRecordsTable({ columns, rows, hideConfidenceColumn }) {
  const showConfidenceColumn = !hideConfidenceColumn && rows.some((row) => !row.hideConfidence);

  return (
    <div className="structured-report-table-wrap">
      <table className="structured-report-table structured-report-records-table">
        <thead>
          <tr>
            {columns.map((column) => (
              <th key={column.field}>{column.label}</th>
            ))}
            {showConfidenceColumn && <th>Confidence</th>}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.id}>
              {columns.map((column) => {
                const cell = row.cells.find((item) => item.id === column.field);
                return (
                  <td data-label={column.label} key={column.field}>
                    {cell?.value || ""}
                  </td>
                );
              })}
              {showConfidenceColumn && (
                <td data-label="Confidence">
                  {!row.hideConfidence && <ReportConfidenceBadge label={row.confidence} />}
                </td>
              )}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function StructuredOperationalSnapshotTable({ rows }) {
  return (
    <div className="structured-report-table-wrap">
      <table className="structured-report-table structured-report-records-table structured-report-operational-snapshot-table">
        <thead>
          <tr>
            <th>Operations</th>
            <th>Value</th>
            <th>Description</th>
            <th>Confidence</th>
          </tr>
        </thead>
        <tbody>
          {rows.flatMap((row) =>
            row.fields.map((field, index) => (
              <tr key={`${row.id}.${field.id}`}>
                {index === 0 ? (
                  <td data-label="Operations" rowSpan={row.fields.length}>{row.operation}</td>
                ) : null}
                <td data-label="Value">
                  <strong className="structured-report-operational-value-label">{field.label}</strong>
                  <span>{field.value}</span>
                </td>
                <td data-label="Description">{field.description}</td>
                <td data-label="Confidence">
                  {!row.hideConfidence && field.confidence ? (
                    <ReportConfidenceBadge label={field.confidence} />
                  ) : null}
                </td>
              </tr>
            )),
          )}
        </tbody>
      </table>
    </div>
  );
}

function toggleIdInList(list, id) {
  return list.includes(id) ? list.filter((itemId) => itemId !== id) : [...list, id];
}

function StructuredReportItem({ item, open, openChildIds, onToggle, onToggleChild }) {
  const itemRowCount = structuredItemRowCount(item);
  const bodyId = `${item.id}-body`;

  return (
    <section className={open ? "structured-report-item open" : "structured-report-item"} id={item.id}>
      <button
        aria-controls={bodyId}
        aria-expanded={open}
        className="structured-report-item-header"
        onClick={onToggle}
        type="button"
      >
        <span className="structured-report-panel-title">
          <span>{item.title}</span>
          {item.description ? <small>{item.description}</small> : null}
        </span>
        <span className="structured-report-panel-meta">{itemRowCount}</span>
        <span className="structured-report-chevron" aria-hidden="true">⌄</span>
      </button>
      {open && (
        <div className="structured-report-item-body" id={bodyId}>
          {item.tableType === "group" ? (
            <div className="structured-report-subsection-stack">
              {(item.children || []).map((child) => {
                const childOpen = openChildIds.includes(child.id);
                const childBodyId = `${child.id}-body`;
                return (
                  <section
                    className={childOpen ? "structured-report-item open" : "structured-report-item"}
                    key={child.id}
                  >
                    <button
                      aria-controls={childBodyId}
                      aria-expanded={childOpen}
                      className="structured-report-item-header"
                      onClick={() => onToggleChild(child.id)}
                      type="button"
                    >
                      <span className="structured-report-panel-title">
                        <span>{child.title}</span>
                        {child.description ? <small>{child.description}</small> : null}
                      </span>
                      <span className="structured-report-panel-meta">{child.rows.length}</span>
                      <span className="structured-report-chevron" aria-hidden="true">⌄</span>
                    </button>
                    {childOpen ? (
                      <div className="structured-report-item-body" id={childBodyId}>
                        {child.tableType === "records" ? (
                          <StructuredReportRecordsTable
                            columns={child.columns}
                            rows={child.rows}
                            hideConfidenceColumn={child.hideConfidence}
                          />
                        ) : child.tableType === "operational_snapshot" ? (
                          <StructuredOperationalSnapshotTable rows={child.rows} />
                        ) : (
                          <StructuredReportKeyValueTable rows={child.rows} hideConfidenceColumn={child.hideConfidence} />
                        )}
                      </div>
                    ) : null}
                  </section>
                );
              })}
            </div>
          ) : item.tableType === "records" ? (
            <StructuredReportRecordsTable
              columns={item.columns}
              rows={item.rows}
              hideConfidenceColumn={item.hideConfidence}
            />
          ) : item.tableType === "operational_snapshot" ? (
            <StructuredOperationalSnapshotTable rows={item.rows} />
          ) : (
            <StructuredReportKeyValueTable rows={item.rows} hideConfidenceColumn={item.hideConfidence} />
          )}
        </div>
      )}
    </section>
  );
}

function StructuredReportSection({ section, open, openItemIds, openChildIdsByItem, onToggle, onToggleItem, onToggleChild }) {
  const bodyId = `${section.id}-body`;

  return (
    <section className={open ? "structured-report-panel open" : "structured-report-panel"} id={section.id}>
      <button
        aria-controls={bodyId}
        aria-expanded={open}
        className="structured-report-panel-header"
        onClick={onToggle}
        type="button"
      >
        <span className="structured-report-panel-number" aria-hidden="true">
          {section.number}
        </span>
        <span className="structured-report-panel-title">
          <span>{section.title}</span>
          {section.description ? <small>{section.description}</small> : null}
        </span>
        <span className="structured-report-panel-meta">{section.rowCount}</span>
        <span className="structured-report-chevron" aria-hidden="true">⌄</span>
      </button>
      {open && (
        <div className="structured-report-panel-body" id={bodyId}>
          <div className="structured-report-subsection-stack">
            {section.items.map((item) => (
              <StructuredReportItem
                key={item.id}
                item={item}
                open={openItemIds.includes(item.id)}
                openChildIds={openChildIdsByItem[item.id] || []}
                onToggle={() => onToggleItem(item.id)}
                onToggleChild={(childId) => onToggleChild(item.id, childId)}
              />
            ))}
          </div>
        </div>
      )}
    </section>
  );
}

function StructuredPreAssessmentReport({ reportData, reportGeneratedDate = "" }) {
  const [activeFilter, setActiveFilter] = useState("All");
  const [menuOpen, setMenuOpen] = useState(false);
  const sections = useMemo(() => makeStructuredReportSections(reportData), [reportData]);
  const [openSectionIds, setOpenSectionIds] = useState(() => sections[0]?.id ? [sections[0].id] : []);
  const [openItemsBySection, setOpenItemsBySection] = useState({});
  const [openChildrenByItem, setOpenChildrenByItem] = useState({});
  const availableFilters = useMemo(() => {
    const counts = { High: 0 };
    sections.forEach((section) => {
      section.items.forEach((item) => collectStructuredConfidenceCounts(item, counts));
    });
    return ["All", ...REPORT_CONFIDENCE_FILTERS.filter((filter) => filter !== "All" && counts[filter] > 0)];
  }, [sections]);
  useEffect(() => {
    if (!availableFilters.includes(activeFilter)) {
      setActiveFilter("All");
    }
  }, [activeFilter, availableFilters]);
  const filteredSections = useMemo(
    () =>
      sections
        .map((section) => {
          const keepSectionUnfiltered = section.id === "account_snapshot";
          const items = section.items
            .map((item) => filterStructuredReportItem(item, activeFilter, keepSectionUnfiltered))
            .filter((item) => structuredItemRowCount(item) > 0);
          return {
            ...section,
            items,
            rowCount: items.reduce((total, item) => total + structuredItemRowCount(item), 0),
          };
        })
        .filter((section) => section.items.length > 0),
    [activeFilter, sections],
  );
  const visibleOpenSectionIds = openSectionIds.filter((sectionId) =>
    filteredSections.some((section) => section.id === sectionId),
  );
  const snapshot = reportData.account_snapshot || {};
  const companyField = unwrapReportField(snapshot.company);
  const facilityField = unwrapReportField(snapshot.facility);
  const facilityValue = isMissingReportValue(facilityField.value)
    ? ""
    : formatStructuredReportValue(facilityField.value);

  function getOpenItemIds(section) {
    if (Object.prototype.hasOwnProperty.call(openItemsBySection, section.id)) {
      return openItemsBySection[section.id].filter((itemId) =>
        section.items.some((item) => item.id === itemId),
      );
    }
    return section.items[0]?.id ? [section.items[0].id] : [];
  }

  function getOpenChildIdsByItem(section) {
    return section.items.reduce((openChildren, item) => {
      const storedIds = openChildrenByItem[item.id];
      if (storedIds) {
        openChildren[item.id] = storedIds.filter((childId) =>
          (item.children || []).some((child) => child.id === childId),
        );
      } else {
        const firstChildId = item.children?.[0]?.id;
        openChildren[item.id] = firstChildId ? [firstChildId] : [];
      }
      return openChildren;
    }, {});
  }

  function toggleItem(sectionId, itemId) {
    setOpenItemsBySection((current) => ({
      ...current,
      [sectionId]: toggleIdInList(current[sectionId] || [], itemId),
    }));
  }

  function toggleChild(itemId, childId) {
    setOpenChildrenByItem((current) => ({
      ...current,
      [itemId]: toggleIdInList(current[itemId] || [], childId),
    }));
  }

  function openSectionFromNav(sectionId) {
    setOpenSectionIds((current) => current.includes(sectionId) ? current : [...current, sectionId]);
    setMenuOpen(false);
    window.setTimeout(() => {
      document.getElementById(sectionId)?.scrollIntoView({
        behavior: "smooth",
        block: "start",
      });
    }, 0);
  }

  if (!filteredSections.length) {
    return (
      <div className="structured-report-empty">
        <h2 className="workspace-page-title">Report data is unavailable</h2>
        <p className="workspace-page-copy">
          The report is marked ready, but the saved report metadata does not match the expected template.
        </p>
      </div>
    );
  }

  return (
    <div className="structured-report-shell">
      <main className="structured-report-main">
        <header className="structured-report-header">
          <div>
            <p className="structured-report-kicker">
              {isMissingReportValue(companyField.value) ? "Assessment Report" : formatStructuredReportValue(companyField.value)}
            </p>
            <h1>{reportStructure.report_ui?.report_title || "Warehouse Automation Pre-Assessment Report"}</h1>
            <p>{reportStructure.report_ui?.report_subtitle || "Operational Intelligence & Automation Fit Analysis"}</p>
          </div>
          {facilityValue || reportGeneratedDate ? (
            <div className="structured-report-header-meta">
              {reportGeneratedDate ? (
                <span className="structured-report-generated-date">
                  Report generated {reportGeneratedDate}
                </span>
              ) : null}
              {facilityValue ? <span>{facilityValue}</span> : null}
            </div>
          ) : null}
          <button
            type="button"
            className="structured-report-menu-button"
            aria-controls="structuredReportSectionMenu"
            aria-expanded={menuOpen}
            aria-label="Toggle report sections"
            onClick={() => setMenuOpen((current) => !current)}
          >
            <span aria-hidden="true"></span>
            <span aria-hidden="true"></span>
            <span aria-hidden="true"></span>
          </button>
        </header>

        {menuOpen ? (
          <nav
            id="structuredReportSectionMenu"
            className="structured-report-section-menu"
            aria-label="Report sections"
          >
            {filteredSections.map((section) => (
              <a
                className={visibleOpenSectionIds.includes(section.id) ? "active" : ""}
                href={`#${section.id}`}
                key={section.id}
                onClick={(event) => {
                  event.preventDefault();
                  openSectionFromNav(section.id);
                }}
              >
                <span className="structured-report-nav-number">{section.number}</span>
                {section.title}
              </a>
            ))}
          </nav>
        ) : null}

        <section className="structured-report-filter-bar" aria-label="Confidence filter">
          {availableFilters.map((filter) => (
            <button
              className={activeFilter === filter ? "active" : ""}
              key={filter}
              onClick={() => setActiveFilter(filter)}
              type="button"
            >
              {filter}
            </button>
          ))}
        </section>

        <div className="structured-report-panel-stack">
          {filteredSections.map((section) => (
            <StructuredReportSection
              key={section.id}
              section={section}
              open={visibleOpenSectionIds.includes(section.id)}
              openItemIds={getOpenItemIds(section)}
              openChildIdsByItem={getOpenChildIdsByItem(section)}
              onToggle={() =>
                setOpenSectionIds((current) => toggleIdInList(current, section.id))
              }
              onToggleItem={(itemId) => toggleItem(section.id, itemId)}
              onToggleChild={toggleChild}
            />
          ))}
        </div>
      </main>
    </div>
  );
}

function StructuredReportUnavailable() {
  return (
    <div className="structured-report-empty">
      <h2 className="workspace-page-title">Report data is unavailable</h2>
      <p className="workspace-page-copy">
        The report is marked ready, but <code>report_metadata</code> does not contain the expected report structure.
      </p>
    </div>
  );
}

function SampleReportPage() {
  const [session] = useRequireSession();
  const location = useLocation();
  const returnToWorkspace = Boolean(location.state?.returnToWorkspace);
  const returnToPreAssessment =
    location.state?.returnToPreAssessment || loadPreAssessmentContext() || {};
  const backLink = returnToWorkspace
    ? { to: "/workspace", state: undefined, label: "Back to workspace" }
    : { to: "/workspace/pre-assessment", state: returnToPreAssessment, label: "Back to pre-assessment" };
  const hasSampleData = hasReportMetadata(brWilliamsSampleReport);

  if (!session?.email) return null;

  return (
    <main className="workspace-page-shell signup-body workspace-body sample-report-page">
      <section className="workspace-page workspace-form-page report-page">
        <div className="report-page-actions">
          <Link
            to={backLink.to}
            state={backLink.state}
            className="btn-primary sample-report-back-link"
          >
            {backLink.label}
          </Link>
        </div>

        <header className="workspace-subpage-head sample-report-head">
          <div className="workspace-subpage-bar">
            <div>
              <p className="workspace-eyebrow">Sample report</p>
              <h1 className="workspace-page-title">BR Williams Pre-Assessment Sample</h1>
              <p className="workspace-page-copy">
                This public sample uses the same structured report layout as a generated site pre-assessment.
              </p>
            </div>
          </div>
        </header>

        <section className="workspace-card workspace-card-modern workspace-card-wide report-view-card">
          {hasSampleData ? (
            <StructuredPreAssessmentReport reportData={brWilliamsSampleReport} />
          ) : (
            <StructuredReportUnavailable />
          )}
        </section>
      </section>
    </main>
  );
}

function AuthExplainerPanel() {
  const steps = [
    {
      title: "Add a Facility",
      paragraphs: [
        "A facility is a warehouse site where intralogistics operations take place.",
        "Create a facility to start an automation project. Once added, you can request assessments, discover opportunities, and manage automation initiatives for that site.",
      ],
    },
    {
      title: "Request a Site Pre-Assessment",
      paragraphs: [
        "A Site Pre-Assessment is a comprehensive research report about a warehouse site.",
        "Before a site visit or sales meeting, Automatisor analyzes the facility across multiple operational dimensions to help you:",
      ],
      bullets: [
        "Understand how the site operates",
        "Quickly determine whether it is a good fit for your solutions",
        "Identify automation opportunities",
        "Discover valuable talking points for customer conversations",
        "Prepare more effective sales pitches and site visits",
      ],
      closing: "Think of it as your automated discovery and qualification process.",
    },
    {
      title: "Discover More Sites",
      paragraphs: [
        "Expand your pipeline by discovering additional warehouse sites.",
        "You can:",
      ],
      bullets: [
        "Find other facilities operated by the same company",
        "Discover nearby warehouses in the area",
        "Build targeted prospecting lists faster",
        "Uncover new automation opportunities",
      ],
      closing: "Use these insights to continuously grow and prioritize your sales pipeline.",
    },
  ];

  return (
    <aside className="auth-explainer-panel" aria-label="Automatisor overview">
      <p className="auth-panel-label">Welcome to Automatisor</p>
      <h1>Automatisor</h1>
      <p className="auth-explainer-lede">
        Automatisor is a decision intelligence platform for warehouse automation. It performs
        the research, analysis, and planning work needed to help you automate warehouses with
        lower risk, faster decisions, higher trust, and better ROI.
      </p>
      <p className="auth-explainer-intro">You can get started in three simple steps:</p>
      <div className="auth-explainer-steps">
        {steps.map((step, index) => (
          <article className="auth-explainer-step" key={step.title}>
            <span className="auth-explainer-step-number">{index + 1}</span>
            <div>
              <h2>{step.title}</h2>
              {step.paragraphs.map((paragraph) => (
                <p key={paragraph}>{paragraph}</p>
              ))}
              {step.bullets?.length ? (
                <ul>
                  {step.bullets.map((item) => (
                    <li key={item}>{item}</li>
                  ))}
                </ul>
              ) : null}
              {step.closing ? <p>{step.closing}</p> : null}
            </div>
          </article>
        ))}
      </div>
    </aside>
  );
}

function normalizeAuthFeedback(rawMessage) {
  const message = String(rawMessage || "").trim();
  if (!message) return { tone: "error", title: "", message: "" };
  if (/token has expired or is invalid/i.test(message) || /invalid or expired otp/i.test(message)) {
    return {
      tone: "error",
      title: "That code didn’t work",
      message: "The OTP is invalid or expired. Request a fresh code and try again.",
    };
  }
  if (/for security purposes/i.test(message) && /15 seconds/i.test(message)) {
    return {
      tone: "info",
      title: "Please wait a moment",
      message: "For security reasons, you can request a new OTP after 15 seconds.",
    };
  }
  if (/fresh otp has been sent/i.test(message)) {
    return {
      tone: "success",
      title: "New code sent",
      message: "A fresh OTP has been sent to your work email.",
    };
  }
  return {
    tone: "error",
    title: "Something needs attention",
    message,
  };
}

function clearSession() {
  try {
    window.sessionStorage.removeItem(SESSION_KEY);
    window.sessionStorage.removeItem(REPORT_CONTEXT_KEY);
    window.sessionStorage.removeItem(PRE_ASSESSMENT_CONTEXT_KEY);
  } catch {
    // Ignore.
  }
}

async function fetchJson(url, options = {}) {
  const res = await fetch(url, {
    credentials: "same-origin",
    headers: {
      "Content-Type": "application/json",
      ...(options.headers || {}),
    },
    ...options,
  });
  const payload = await res.json().catch(() => ({}));
  if (!res.ok) {
    const error = new Error(payload.detail || "Request failed.");
    error.code = payload.code || res.headers.get("x-error-code") || "";
    error.payload = payload;
    throw error;
  }
  return payload;
}

async function getFrontendConfig() {
  if (!frontendConfigPromise) {
    frontendConfigPromise = fetchJson("/api/frontend-config", { method: "GET" });
  }
  return frontendConfigPromise;
}

async function loadGoogleMaps() {
  if (window.google && window.google.maps && window.google.maps.importLibrary) {
    return window.google;
  }
  if (!googleMapsPromise) {
    googleMapsPromise = getFrontendConfig().then((config) => {
      const apiKey = config.google_maps_api_key;
      if (!apiKey) {
        throw new Error("Missing GOOGLE_MAPS_API_KEY in backend configuration.");
      }
      return new Promise((resolve, reject) => {
        if (window.google && window.google.maps && window.google.maps.importLibrary) {
          resolve(window.google);
          return;
        }

        const existingBootstrap = document.querySelector('script[data-google-maps-loader="bootstrap"]');
        if (existingBootstrap) {
          waitForGoogleFeature(() => window.google?.maps?.importLibrary, 5000)
            .then((value) => {
              if (value) resolve(window.google);
              else reject(new Error("Could not load Google Maps."));
            })
            .catch(() => reject(new Error("Could not load Google Maps.")));
          return;
        }

        const googleNS = (window.google = window.google || {});
        const mapsNS = (googleNS.maps = googleNS.maps || {});
        const bootstrapCallback = "__ib__";
        const requestedLibraries = new Set();
        let bootstrapPromise = null;

        const ensureBootstrap = () => {
          if (bootstrapPromise) return bootstrapPromise;
          bootstrapPromise = new Promise((innerResolve, innerReject) => {
            const script = document.createElement("script");
            const params = new URLSearchParams();
            params.set("key", apiKey);
            params.set("v", "beta");
            params.set("loading", "async");
            if (requestedLibraries.size) {
              params.set("libraries", Array.from(requestedLibraries).join(","));
            }
            params.set("callback", `google.maps.${bootstrapCallback}`);
            script.src = `https://maps.googleapis.com/maps/api/js?${params.toString()}`;
            script.async = true;
            script.defer = true;
            script.dataset.googleMapsLoader = "bootstrap";
            mapsNS[bootstrapCallback] = () => innerResolve(window.google);
            script.onerror = () => innerReject(new Error("Could not load Google Maps."));
            document.head.appendChild(script);
          });
          return bootstrapPromise;
        };

        mapsNS.importLibrary =
          mapsNS.importLibrary ||
          ((libraryName, ...args) => {
            requestedLibraries.add(libraryName);
            return ensureBootstrap().then(() => mapsNS.importLibrary(libraryName, ...args));
          });

        mapsNS
          .importLibrary("places")
          .then(() => resolve(window.google))
          .catch(() => reject(new Error("Could not load Google Maps.")));
      });
    });
  }
  return googleMapsPromise;
}

async function waitForGoogleFeature(check, timeoutMs = 2500) {
  const startedAt = Date.now();
  while (Date.now() - startedAt < timeoutMs) {
    const value = check();
    if (value) return value;
    await new Promise((resolve) => window.setTimeout(resolve, 50));
  }
  return null;
}

function normalizeEmail(raw) {
  return String(raw || "").trim().toLowerCase();
}

function workEmailError(value) {
  const email = normalizeEmail(value);
  if (!email) return "Work email is required.";
  if (!email.includes("@") || /\s/.test(email)) return "Enter a valid work email.";
  const parts = email.split("@");
  if (parts.length !== 2) return "Enter a valid work email.";
  if (!parts[0] || !parts[1] || !parts[1].includes(".")) return "Enter a valid work email.";
  if (isFreeEmail(email)) return "Personal email addresses are not accepted.";
  return "";
}

function cityFromFormattedAddress(formattedAddress) {
  const parts = String(formattedAddress || "")
    .split(",")
    .map((part) => part.trim())
    .filter(Boolean);
  if (parts.length < 3) return "";
  const candidate = parts[1] || "";
  return /county$/i.test(candidate) ? "" : candidate;
}

function structuredAddressFromComponents(formattedAddress, components) {
  const byType = {};
  (components || []).forEach((component) => {
    (component.types || []).forEach((type) => {
      if (!byType[type]) {
        byType[type] = component;
      }
    });
  });

  const longText = (component) =>
    component?.long_name || component?.longText || component?.componentText || "";
  const shortText = (component) =>
    component?.short_name || component?.shortText || component?.componentTextShortForm || "";

  const streetParts = [
    longText(byType.street_number),
    longText(byType.route),
  ].filter(Boolean);
  const formattedCity = cityFromFormattedAddress(formattedAddress);
  const county = longText(byType.administrative_area_level_2);

  return {
    full_address: String(formattedAddress || "").trim(),
    street: streetParts.join(" ").trim(),
    city:
      longText(byType.locality) ||
      longText(byType.postal_town) ||
      longText(byType.sublocality) ||
      formattedCity ||
      (/county$/i.test(county) ? "" : county) ||
      "",
    state: String(
      shortText(byType.administrative_area_level_1) ||
        longText(byType.administrative_area_level_1) ||
        "",
    )
      .trim()
      .toUpperCase(),
    zip: String(longText(byType.postal_code) || "").trim(),
    country: String(shortText(byType.country) || "US").trim().toUpperCase(),
    place_id: "",
  };
}

function buildGoogleMapsSearchLink(address) {
  const value = String(address || "").trim();
  return value
    ? `https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(value)}`
    : "";
}

function normalizeResolvedAddress(siteLike) {
  if (!siteLike || typeof siteLike !== "object") return null;
  const fullAddress = String(siteLike.full_address || siteLike.fullAddress || "").trim();
  if (!fullAddress) return null;
  return {
    full_address: fullAddress,
    street: siteLike.street || "",
    city: siteLike.city || "",
    state: siteLike.state || "",
    zip: siteLike.zip || "",
    country: siteLike.country || "US",
    place_id: siteLike.place_id || "",
    lat: Number.isFinite(Number(siteLike.lat)) ? Number(siteLike.lat) : null,
    lng: Number.isFinite(Number(siteLike.lng)) ? Number(siteLike.lng) : null,
  };
}

function normalizeCandidateDomain(candidate) {
  const website = String(candidate?.website || "").trim();
  if (!website) return "";
  try {
    const url = website.includes("://") ? new URL(website) : new URL(`https://${website}`);
    return url.hostname.replace(/^www\./, "").toLowerCase();
  } catch {
    return website
      .split("/")[0]
      .split(":")[0]
      .replace(/^www\./, "")
      .toLowerCase();
  }
}

function candidateKey(candidate) {
  return String(candidate?.place_id || `${candidate?.name || ""}|${candidate?.address || ""}`);
}

function resolvedAddressFromCandidate(candidate, fallback = {}) {
  const address = String(candidate?.address || "").trim();
  if (!address) return null;
  const location = candidate?.location || {};
  return {
    full_address: address,
    street: "",
    city: cityFromFormattedAddress(address),
    state: "",
    zip: "",
    country: "US",
    place_id: candidate?.place_id || fallback.place_id || "",
    lat: Number.isFinite(Number(location.lat)) ? Number(location.lat) : null,
    lng: Number.isFinite(Number(location.lng)) ? Number(location.lng) : null,
  };
}

function validationRequestBasis(validation, selectedCandidate, justification) {
  if (selectedCandidate) return "candidate_selected";
  if (String(justification || "").trim()) return "manual_justification";
  if (validation?.status === "validated") return "validated";
  if (validation?.status === "unavailable") return "validation_unavailable";
  return "";
}

function initialSelectedCandidateFromDraft(draft) {
  return draft?.request_basis === "candidate_selected" && draft?.selected_candidate
    ? draft.selected_candidate
    : null;
}

function initialJustificationFromDraft(draft) {
  return draft?.request_basis === "manual_justification" ? draft?.justification || "" : "";
}

function useAutomaticAddressValidation({
  companyName,
  domain,
  resolvedAddress,
  initialSelectedCandidate = null,
  initialJustification = "",
}) {
  const [validation, setValidation] = useState(null);
  const [validationError, setValidationError] = useState("");
  const [checking, setChecking] = useState(false);
  const [selectedCandidate, setSelectedCandidate] = useState(initialSelectedCandidate);
  const [justification, setJustification] = useState(initialJustification);
  const normalizedCompany = String(companyName || "").trim();
  const normalizedDomain = String(domain || "").trim();
  const normalizedAddress = String(resolvedAddress?.full_address || "").trim();
  const validationKey = `${normalizedCompany}|${normalizedDomain}|${normalizedAddress}`;
  const hasValidationInputs = Boolean(normalizedCompany && normalizedDomain && normalizedAddress);

  useEffect(() => {
    setValidation(null);
    setValidationError("");
    if (!hasValidationInputs) {
      setChecking(false);
      return undefined;
    }

    let active = true;
    setChecking(true);
    const timer = window.setTimeout(() => {
      fetchJson("/api/address-validation/check", {
        method: "POST",
        body: JSON.stringify({
          company_name: normalizedCompany,
          domain: normalizedDomain,
          address: normalizedAddress,
        }),
      })
        .then((payload) => {
          if (!active) return;
          setValidation(payload);
          setValidationError("");
        })
        .catch((error) => {
          if (!active) return;
          setValidation(null);
          setValidationError(error.message || "Could not validate this company and address.");
        })
        .finally(() => {
          if (active) setChecking(false);
        });
    }, 650);

    return () => {
      active = false;
      window.clearTimeout(timer);
    };
  }, [validationKey, hasValidationInputs, normalizedCompany, normalizedDomain, normalizedAddress]);

  const basis = validationRequestBasis(validation, selectedCandidate, justification);
  const canProceed = Boolean(
    hasValidationInputs &&
      !checking &&
      !validationError &&
      (validation?.status === "validated" ||
        validation?.status === "unavailable" ||
        selectedCandidate ||
        String(justification || "").trim()),
  );

  return {
    validation,
    validationError,
    checking,
    selectedCandidate,
    setSelectedCandidate,
    justification,
    setJustification,
    resetOverrides() {
      setSelectedCandidate(null);
      setJustification("");
    },
    hasValidationInputs,
    canProceed,
    requestBasis: basis,
  };
}

function AddressValidationPanel({
  validation,
  validationError,
  checking,
  selectedCandidate,
  onSelectCandidate,
  justification,
  onJustificationChange,
  hasValidationInputs,
  domain,
  addressLabel = "Site address",
}) {
  if (!hasValidationInputs) {
    if (selectedCandidate) {
      return (
        <div className="address-validation-panel address-validation-panel-warning">
          Enter the company domain before requesting the pre-assessment.
        </div>
      );
    }
    return null;
  }

  if (checking) {
    return (
      <div className="address-validation-panel address-validation-panel-checking">
        Checking whether this company exists at the selected address...
      </div>
    );
  }

  if (validationError) {
    return (
      <div className="address-validation-panel address-validation-panel-error">
        {validationError}
      </div>
    );
  }

  if (!validation) return null;

  if (validation.status === "validated") {
    return null;
  }

  if (validation.status === "unavailable") {
    return (
      <div className="address-validation-panel address-validation-panel-neutral">
        Address validation is temporarily unavailable. You can continue.
      </div>
    );
  }

  const candidates = Array.isArray(validation.candidates) ? validation.candidates : [];

  return (
    <section className="address-validation-panel address-validation-panel-warning">
      <div className="address-validation-head">
        <h3>We could not find this company at that address.</h3>
        <p>Here are a few things you could do.</p>
      </div>
      <div className="address-validation-options-table">
        <div className="address-validation-option-row">
          <div className="address-validation-option-label">Option 1</div>
          <div className="address-validation-option-content">
            <h4>Edit the address</h4>
            <p>
              Please edit the address in the <strong>{addressLabel}</strong> field above.
            </p>
          </div>
        </div>
        <div className="address-validation-option-row">
          <div className="address-validation-option-label">Option 2</div>
          <div className="address-validation-option-content">
            <h4>Identified companies at this address</h4>
            <p>
              We have found the below companies at the address you specified, if you want to do a
              pre-assessment for one of these companies instead then click the{" "}
              <strong>Use this</strong> button.
            </p>
            {candidates.length ? (
              <div className="address-candidate-list">
                {candidates.map((candidate, index) => {
                  const selected = candidateKey(selectedCandidate) === candidateKey(candidate);
                  return (
                    <article
                      className={`address-candidate ${selected ? "address-candidate-selected" : ""}`}
                      key={candidate.place_id || `${candidate.name}-${index}`}
                    >
                      <div className="address-candidate-copy">
                        <strong>{candidate.name || "Unnamed place"}</strong>
                        <span>{candidate.address || "No address returned"}</span>
                        <span>{candidate.website || "No website returned"}</span>
                      </div>
                      <div className="address-candidate-actions">
                        {candidate.google_maps_uri ? (
                          <a
                            className="address-candidate-map-link"
                            href={candidate.google_maps_uri}
                            target="_blank"
                            rel="noopener noreferrer"
                          >
                            Maps
                          </a>
                        ) : null}
                        <button
                          type="button"
                          className={selected ? "btn-secondary" : "btn-primary"}
                          onClick={() => {
                            if (!selected) onSelectCandidate(candidate);
                          }}
                          disabled={selected}
                        >
                          {selected ? "Selected" : "Use this"}
                        </button>
                      </div>
                    </article>
                  );
                })}
              </div>
            ) : (
              <p className="address-validation-empty">No candidates identified at this address.</p>
            )}
          </div>
        </div>
        <div className="address-validation-option-row">
          <div className="address-validation-option-label">Option 3</div>
          <div className="address-validation-option-content">
            <h4>Justification</h4>
            <p>
              If you're sure of the company name and the address it is located at, please
              give us more information regarding it in the text area below.
            </p>
            <label className="workspace-field address-justification-field">
              <span>Reason</span>
              <textarea
                value={justification}
                onChange={(event) => onJustificationChange(event.target.value)}
                placeholder="Example: This company ships from this address through PartnerCo's warehouse. Mention the partner company name if there is one, and explain the relationship so the report has better context."
              />
            </label>
          </div>
        </div>
      </div>
      {selectedCandidate && !String(domain || "").trim() ? (
        <p className="address-validation-domain-hint">
          Enter the company domain before requesting the pre-assessment.
        </p>
      ) : null}
    </section>
  );
}

function CandidateConfirmationModal({
  candidate,
  loading,
  onCancel,
  onConfirm,
  addressLabel = "Site Address",
}) {
  if (!candidate) return null;
  const domain = normalizeCandidateDomain(candidate);
  return (
    <div className="review-modal-backdrop" role="presentation">
      <section
        className="review-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="candidateConfirmTitle"
      >
        <div className="review-modal-head">
          <p className="workspace-eyebrow">Confirm candidate</p>
          <h2 id="candidateConfirmTitle" className="workspace-card-title">
            Use this candidate?
          </h2>
        </div>
        <p className="workspace-copy">
          Confirming will update the company name, domain, {addressLabel.toLowerCase()}, and map pin for this
          request.
        </p>
        <div className="pre-assessment-summary-grid review-summary-grid">
          <div className="workspace-summary-chip">
            <span className="workspace-summary-label">Company Name</span>
            <span className="workspace-summary-value">{candidate.name || "-"}</span>
          </div>
          <div className="workspace-summary-chip">
            <span className="workspace-summary-label">{addressLabel}</span>
            <span className="workspace-summary-value">{candidate.address || "-"}</span>
          </div>
          <div className="workspace-summary-chip">
            <span className="workspace-summary-label">Domain</span>
            <span className="workspace-summary-value">{domain || "No website returned"}</span>
          </div>
        </div>
        <div className="review-modal-actions">
          <button type="button" className="btn-secondary" onClick={onCancel} disabled={loading}>
            Cancel
          </button>
          <button type="button" className="btn-primary" onClick={onConfirm} disabled={loading}>
            {loading ? "Please wait..." : "Confirm candidate"}
          </button>
        </div>
      </section>
    </div>
  );
}

const GoogleAddressPicker = forwardRef(function GoogleAddressPicker(
  {
    inputId,
    googleButtonId,
    mapId,
    messageId,
    onResolvedChange,
    mapLabel,
    initialResolvedAddress,
    addressLabel = "Site address",
    addressPlaceholder = "Start typing a site address",
  },
  ref,
) {
  const initialResolved = useMemo(
    () => normalizeResolvedAddress(initialResolvedAddress),
    [initialResolvedAddress],
  );
  const [selectedAddress, setSelectedAddress] = useState(
    () => initialResolved?.full_address || "",
  );
  const [message, setStatusMessage] = useState("");
  const [messageIsError, setMessageIsError] = useState(false);
  const [mapsLink, setMapsLink] = useState(
    () => buildGoogleMapsSearchLink(initialResolved?.full_address || ""),
  );
  const [isGoogleReady, setIsGoogleReady] = useState(false);
  const [useLegacyAutocomplete, setUseLegacyAutocomplete] = useState(false);
  const resolvedRef = useRef(initialResolved?.full_address ? initialResolved : null);
  const inputRef = useRef(null);
  const autocompleteHostRef = useRef(null);
  const autocompleteElementRef = useRef(null);
  const legacyAutocompleteRef = useRef(null);
  const mapNodeRef = useRef(null);
  const mapRef = useRef(null);
  const markerRef = useRef(null);
  const geocoderRef = useRef(null);
  const initializedModeRef = useRef(null);

  function syncVisibleAddress(nextAddress) {
    const value = String(nextAddress || "");
    if (inputRef.current) {
      inputRef.current.value = value;
    }
    if (autocompleteElementRef.current) {
      try {
        autocompleteElementRef.current.value = value;
      } catch {
        // Ignore unsupported host assignments.
      }
      try {
        autocompleteElementRef.current.setAttribute("value", value);
      } catch {
        // Ignore unsupported host assignments.
      }
    }
  }

  function publishResolved(nextResolved) {
    resolvedRef.current = nextResolved;
    const nextAddress = nextResolved?.full_address || "";
    setSelectedAddress(nextAddress);
    setMapsLink(buildGoogleMapsSearchLink(nextAddress));
    syncVisibleAddress(nextAddress);
    if (typeof onResolvedChange === "function") {
      onResolvedChange({
        resolvedAddress: nextResolved,
        inputValue: nextAddress,
      });
    }
  }

  function moveMapToLocation(location) {
    if (!location || !markerRef.current || !mapRef.current) return false;
    const lat = Number(location.lat);
    const lng = Number(location.lng);
    if (!Number.isFinite(lat) || !Number.isFinite(lng)) return false;
    const nextLocation = { lat, lng };
    markerRef.current.setPosition(nextLocation);
    mapRef.current.panTo(nextLocation);
    mapRef.current.setZoom(19);
    return true;
  }

  useEffect(() => {
    if (!initialResolved?.full_address) return;
    publishResolved(initialResolved);
  }, [initialResolved?.full_address]);

  async function reverseGeocodeLocation(location) {
    const geocoder = geocoderRef.current;
    if (!geocoder) {
      throw new Error("Map geocoder is not ready.");
    }
    const result = await geocoder.geocode({ location });
    const top = result.results && result.results[0];
    if (!top) {
      throw new Error("Could not resolve that address.");
    }
    const nextResolved = structuredAddressFromComponents(
      top.formatted_address,
      top.address_components,
    );
    nextResolved.place_id = top.place_id || "";
    publishResolved(nextResolved);
    return nextResolved;
  }

  useImperativeHandle(
    ref,
    () => ({
      async resolveCurrentAddress() {
      if (resolvedRef.current?.full_address) {
        return { ...resolvedRef.current };
      }
      throw new Error("Select a valid address from the dropdown or map first.");
      },
      getCurrentInput() {
        return String(resolvedRef.current?.full_address || "").trim();
      },
      async applyResolvedAddress(nextAddress) {
        const normalized = normalizeResolvedAddress(nextAddress);
        if (!normalized?.full_address) {
          throw new Error("Candidate address is missing.");
        }
        let resolvedForPublish = { ...normalized };
        if (geocoderRef.current && normalized.full_address) {
          const result = await geocoderRef.current.geocode({ address: normalized.full_address });
          const top = result.results && result.results[0];
          if (top) {
            resolvedForPublish = {
              ...structuredAddressFromComponents(
                top.formatted_address || normalized.full_address,
                top.address_components || [],
              ),
              full_address: normalized.full_address,
              place_id: normalized.place_id || top.place_id || "",
            };
            if (top.geometry?.location) {
              resolvedForPublish.lat = top.geometry.location.lat();
              resolvedForPublish.lng = top.geometry.location.lng();
            }
          }
        }
        publishResolved(resolvedForPublish);
        if (moveMapToLocation(nextAddress) || moveMapToLocation(resolvedForPublish)) {
          showStatusMessage("Address updated from the selected candidate.", false);
          return { ...resolvedForPublish };
        }
        showStatusMessage("Address updated from the selected candidate.", false);
        return { ...resolvedForPublish };
      },
    }),
    [],
  );

  useEffect(() => {
    let active = true;

    loadGoogleMaps()
      .then((google) => {
        const initialize = async () => {
          const importLibraryFn =
            (await waitForGoogleFeature(() => google.maps.importLibrary)) || google.maps.importLibrary;
          const directPlaceAutocompleteCtor =
            (await waitForGoogleFeature(() => google.maps.places?.PlaceAutocompleteElement)) ||
            google.maps.places?.PlaceAutocompleteElement;
          const directLegacyAutocompleteCtor =
            (await waitForGoogleFeature(() => google.maps.places?.Autocomplete)) ||
            google.maps.places?.Autocomplete;

          let MapCtor = (await waitForGoogleFeature(() => google.maps.Map)) || google.maps.Map;
          let MarkerCtor = (await waitForGoogleFeature(() => google.maps.Marker)) || google.maps.Marker;
          let GeocoderCtor = (await waitForGoogleFeature(() => google.maps.Geocoder)) || google.maps.Geocoder;
          let PlaceAutocompleteCtor = directPlaceAutocompleteCtor || null;

          if (importLibraryFn) {
            const [mapsLibrary, placesLibrary, geocodingLibrary] = await Promise.all([
              importLibraryFn("maps"),
              importLibraryFn("places"),
              importLibraryFn("geocoding"),
            ]);
            MapCtor = mapsLibrary?.Map || google.maps.Map;
            MarkerCtor = google.maps.Marker;
            GeocoderCtor = geocodingLibrary?.Geocoder || google.maps.Geocoder;
            PlaceAutocompleteCtor =
              placesLibrary?.PlaceAutocompleteElement ||
              directPlaceAutocompleteCtor ||
              google.maps.places?.PlaceAutocompleteElement;
          }

          writeAutocompleteDebug("loader_state", {
            hasImportLibrary: Boolean(importLibraryFn),
            hasDirectPlaceAutocompleteCtor: Boolean(directPlaceAutocompleteCtor),
            hasDirectLegacyAutocompleteCtor: Boolean(directLegacyAutocompleteCtor),
            hasResolvedPlaceAutocompleteCtor: Boolean(PlaceAutocompleteCtor),
            hasMapCtor: Boolean(MapCtor),
            hasMarkerCtor: Boolean(MarkerCtor),
            hasGeocoderCtor: Boolean(GeocoderCtor),
          });

          const useLegacy = !PlaceAutocompleteCtor && Boolean(directLegacyAutocompleteCtor);
          writeAutocompleteDebug("mode_decision", {
            useLegacy,
            currentStateUseLegacy: useLegacyAutocomplete,
          });
          if (useLegacy !== useLegacyAutocomplete) {
            setUseLegacyAutocomplete(useLegacy);
            return;
          }

          if (
            !active ||
            !mapNodeRef.current ||
            !inputRef.current ||
            (!useLegacy && !autocompleteHostRef.current)
          ) {
            return;
          }

          if (initializedModeRef.current === (useLegacy ? "legacy" : "new")) {
            writeAutocompleteDebug("skip_reinitialize", {
              mode: initializedModeRef.current,
            });
            return;
          }

          if (!MapCtor || !MarkerCtor || !GeocoderCtor) {
            const debug = {
              hasGoogle: Boolean(window.google),
              hasMapsNamespace: Boolean(window.google?.maps),
              hasImportLibrary: Boolean(importLibraryFn),
              hasMapCtor: Boolean(MapCtor),
              hasMarkerCtor: Boolean(MarkerCtor),
              hasGeocoderCtor: Boolean(GeocoderCtor),
              hasPlaceAutocompleteCtor: Boolean(PlaceAutocompleteCtor),
              hasLegacyAutocompleteCtor: Boolean(directLegacyAutocompleteCtor),
              useLegacy,
            };
            console.error("Google Maps library load failure", debug);
            throw new Error(`Google Maps libraries did not load correctly: ${JSON.stringify(debug)}`);
          }

          if (!useLegacy && !PlaceAutocompleteCtor) {
            const debug = {
              hasGoogle: Boolean(window.google),
              hasMapsNamespace: Boolean(window.google?.maps),
              hasImportLibrary: Boolean(importLibraryFn),
              hasPlaceAutocompleteCtor: Boolean(PlaceAutocompleteCtor),
              hasLegacyAutocompleteCtor: Boolean(directLegacyAutocompleteCtor),
            };
            console.error("Google autocomplete unavailable", debug);
            throw new Error(
              `Google autocomplete is unavailable in this runtime: ${JSON.stringify(debug)}`,
            );
          }

        geocoderRef.current = new GeocoderCtor();
        mapRef.current = new MapCtor(mapNodeRef.current, {
          center: DEFAULT_SITE_COORDS,
          zoom: 19,
          mapTypeId: "satellite",
          streetViewControl: false,
          fullscreenControl: false,
          mapTypeControl: false,
        });
        markerRef.current = new MarkerCtor({
          map: mapRef.current,
          position: DEFAULT_SITE_COORDS,
          draggable: true,
        });
        writeAutocompleteDebug("map_initialized", {
          mode: useLegacy ? "legacy" : "new",
          inputPresent: Boolean(inputRef.current),
          hostPresent: Boolean(autocompleteHostRef.current),
        });

        if (useLegacy) {
          autocompleteHostRef.current.innerHTML = "";
          legacyAutocompleteRef.current = new directLegacyAutocompleteCtor(inputRef.current, {
            fields: ["formatted_address", "geometry", "address_components", "place_id", "name"],
            componentRestrictions: { country: "us" },
          });
          writeAutocompleteDebug("legacy_autocomplete_initialized", {
            inputId,
            inputValue: inputRef.current?.value || "",
          });
          inputRef.current?.addEventListener("input", () => {
            writeAutocompleteDebug("legacy_input_change", {
              value: inputRef.current?.value || "",
            });
          });
          legacyAutocompleteRef.current.addListener("place_changed", () => {
            const place = legacyAutocompleteRef.current.getPlace();
            writeAutocompleteDebug("legacy_place_changed", {
              hasPlace: Boolean(place),
              hasGeometry: Boolean(place?.geometry?.location),
              formattedAddress: place?.formatted_address || "",
              placeId: place?.place_id || "",
            });
            if (!place || !place.geometry?.location) {
              showStatusMessage("Select a valid address from the Google suggestions.", true);
              return;
            }
            const nextResolved = structuredAddressFromComponents(
              place.formatted_address || place.name || "",
              place.address_components || [],
            );
            nextResolved.place_id = place.place_id || "";
            if (inputRef.current) {
              inputRef.current.value = nextResolved.full_address;
            }
            publishResolved(nextResolved);
            markerRef.current.setPosition(place.geometry.location);
            mapRef.current.panTo(place.geometry.location);
            mapRef.current.setZoom(19);
            showStatusMessage("Address found and loaded into the field.", false);
          });
        } else {
          const placeAutocomplete = new PlaceAutocompleteCtor({
            includedRegionCodes: ["us"],
            requestedRegion: "us",
            requestedLanguage: "en",
          });
          placeAutocomplete.setAttribute("placeholder", addressPlaceholder);
          placeAutocomplete.className = "place-autocomplete-host-element";
          placeAutocomplete.style.width = "100%";
          placeAutocomplete.style.backgroundColor = "#ffffff";
          placeAutocomplete.style.border = "1px solid rgba(17, 24, 39, 0.12)";
          placeAutocomplete.style.borderRadius = "16px";
          placeAutocomplete.style.colorScheme = "light";
          autocompleteHostRef.current.innerHTML = "";
          autocompleteHostRef.current.appendChild(placeAutocomplete);
          autocompleteElementRef.current = placeAutocomplete;
          writeAutocompleteDebug("new_autocomplete_initialized", {
            hostId: inputId,
            tagName: placeAutocomplete.tagName,
          });

          placeAutocomplete.addEventListener("input", () => {
            writeAutocompleteDebug("new_input_change", {
              value: placeAutocomplete.value || "",
            });
            if (resolvedRef.current) {
              publishResolved(null);
            }
          });

          placeAutocomplete.addEventListener("keydown", (event) => {
            writeAutocompleteDebug("new_keydown", {
              key: event.key,
            });
            if (
              resolvedRef.current &&
              (event.key.length === 1 || event.key === "Backspace" || event.key === "Delete")
            ) {
              publishResolved(null);
            }
          });

          placeAutocomplete.addEventListener("gmp-select", async (event) => {
            const placePrediction =
              event.placePrediction || event.detail?.placePrediction || event.detail?.prediction;
            writeAutocompleteDebug("new_place_select", {
              hasPrediction: Boolean(placePrediction),
            });
            if (!placePrediction) {
              showStatusMessage("Select a valid address from the Google suggestions.", true);
              return;
            }
            const place = placePrediction.toPlace();
            await place.fetchFields({
              fields: ["displayName", "formattedAddress", "location", "addressComponents", "id"],
            });
            writeAutocompleteDebug("new_place_fields_fetched", {
              formattedAddress: place.formattedAddress || "",
              placeId: place.id || "",
              hasLocation: Boolean(place.location),
            });
            if (!place.location) {
              showStatusMessage("Select a valid address from the Google suggestions.", true);
              return;
            }
            const nextResolved = structuredAddressFromComponents(
              place.formattedAddress || place.displayName || "",
              place.addressComponents || [],
            );
            nextResolved.place_id = place.id || "";
            publishResolved(nextResolved);
            markerRef.current.setPosition(place.location);
            mapRef.current.panTo(place.location);
            mapRef.current.setZoom(19);
            showStatusMessage("Address found and loaded into the field.", false);
          });
        }

        markerRef.current.addListener("dragend", async () => {
          try {
            const position = markerRef.current.getPosition();
            await reverseGeocodeLocation(position);
            showStatusMessage("Address updated from the map pin.", false);
          } catch (error) {
            showStatusMessage(error.message || "Could not resolve that address.", true);
          }
        });

        mapRef.current.addListener("click", async (event) => {
          try {
            markerRef.current.setPosition(event.latLng);
            await reverseGeocodeLocation(event.latLng);
            showStatusMessage("Address updated from the map pin.", false);
          } catch (error) {
            showStatusMessage(error.message || "Could not resolve that address.", true);
          }
        });

        if (resolvedRef.current?.full_address) {
          syncVisibleAddress(resolvedRef.current.full_address);
          setMapsLink(buildGoogleMapsSearchLink(resolvedRef.current.full_address));
          if (initialResolved?.lat && initialResolved?.lng) {
            moveMapToLocation(initialResolved);
          } else {
            try {
              const result = await geocoderRef.current.geocode({
                address: resolvedRef.current.full_address,
              });
              const top = result.results && result.results[0];
              if (top?.geometry?.location) {
                moveMapToLocation({
                  lat: top.geometry.location.lat(),
                  lng: top.geometry.location.lng(),
                });
              }
            } catch {
              // Keep the default map center if the draft address cannot be geocoded.
            }
          }
        }
        setIsGoogleReady(true);
        initializedModeRef.current = useLegacy ? "legacy" : "new";
        window.__automatisorAutocompleteInspect = () => ({
          useLegacyAutocomplete,
          initializedMode: initializedModeRef.current,
          inputValue: inputRef.current?.value || "",
          hostChildren: autocompleteHostRef.current?.children?.length || 0,
          hasLegacyInstance: Boolean(legacyAutocompleteRef.current),
          hasNewInstance: Boolean(autocompleteElementRef.current),
          pacContainerCount: document.querySelectorAll(".pac-container").length,
          debugLog: window.__automatisorAutocompleteDebug || [],
        });
        };

        return initialize();
      })
      .catch((error) => {
        if (!active) return;
        showStatusMessage(error.message || "Could not load Google Maps.", true);
      });

    return () => {
      active = false;
    };
  }, [useLegacyAutocomplete]);

  function showStatusMessage(nextMessage, isError) {
    setMessageIsError(Boolean(isError));
    setStatusMessage(nextMessage || "");
  }

  return (
    <>
      <label className="modern-field modern-field-wide workspace-field workspace-field-wide">
        <span>{addressLabel}</span>
        {useLegacyAutocomplete ? (
          <div className="workspace-search-stack">
            <input
              id={inputId}
              ref={inputRef}
              type="text"
              className="place-autocomplete-legacy-input"
              placeholder={addressPlaceholder}
              autoComplete="off"
              defaultValue={selectedAddress}
              onChange={() => {
                if (resolvedRef.current) {
                  publishResolved(null);
                }
              }}
            />
            <div className="workspace-search-action">
              <a
                id={googleButtonId}
                className={`btn-secondary ${mapsLink ? "" : "hidden"}`}
                href={mapsLink || undefined}
                target="_blank"
                rel="noopener noreferrer"
              >
                Open in Google Maps
              </a>
            </div>
          </div>
        ) : (
          <>
            <div
              ref={autocompleteHostRef}
              id={inputId}
              className={`place-autocomplete-mount ${isGoogleReady ? "" : "place-autocomplete-mount-loading"}`}
            ></div>
            <div className="workspace-search-action">
              <a
                id={googleButtonId}
                className={`btn-secondary ${mapsLink ? "" : "hidden"}`}
                href={mapsLink || undefined}
                target="_blank"
                rel="noopener noreferrer"
              >
                Open in Google Maps
              </a>
            </div>
            <input
              ref={inputRef}
              type="text"
              className="place-autocomplete-shadow-input"
              aria-hidden="true"
              tabIndex={-1}
              readOnly
            />
          </>
        )}
      </label>

      <div className="workspace-map-cluster workspace-field-wide">
        <div className="workspace-map-shell">
          <div
            id={mapId}
            ref={mapNodeRef}
            className="workspace-map-frame"
            aria-label={mapLabel}
          ></div>
        </div>
        <p
          id={messageId}
          className={`workspace-feedback ${message ? "" : "hidden"} ${
            messageIsError ? "workspace-feedback-error" : ""
          }`}
        >
          {message}
        </p>
      </div>
    </>
  );
});

// ── Profile menu (avatar circle + dropdown) ────────────────
function ProfileMenu({ session, onLogout }) {
  const [open, setOpen] = useState(false);
  const menuRef = useRef(null);

  useEffect(() => {
    function handleOutsideClick(e) {
      if (menuRef.current && !menuRef.current.contains(e.target)) {
        setOpen(false);
      }
    }
    if (open) document.addEventListener("mousedown", handleOutsideClick);
    return () => document.removeEventListener("mousedown", handleOutsideClick);
  }, [open]);

  const initials = (session?.email || "?").charAt(0).toUpperCase();

  return (
    <div className="profile-menu" ref={menuRef}>
      <button
        type="button"
        className="profile-avatar-btn"
        onClick={() => setOpen((v) => !v)}
        aria-label="Open profile menu"
        aria-expanded={open}
      >
        {initials}
      </button>

      {open && (
        <div className="profile-dropdown" role="dialog" aria-label="Profile">
          {/* User info */}
          <div className="profile-dropdown-header">
            <div className="profile-dropdown-avatar">{initials}</div>
            <div className="profile-dropdown-info">
              <p className="profile-dropdown-email">{session?.email}</p>
            </div>
          </div>

          <div className="profile-dropdown-divider" />

          {/* Payment method */}
          <div className="profile-dropdown-section">
            <div className="profile-payment-row">
              <span className="profile-section-label">Payment method</span>
              <Link
                to="/workspace/billing"
                className="btn-secondary btn-sm"
                onClick={() => setOpen(false)}
              >
                Manage card
              </Link>
            </div>
          </div>

          <div className="profile-dropdown-divider" />

          {/* Logout */}
          <button
            type="button"
            className="profile-logout-btn"
            onClick={() => { setOpen(false); onLogout(); }}
          >
            Log out
          </button>
        </div>
      )}
    </div>
  );
}

function CreditsUsedChip({ creditsUsed }) {
  return (
    <div className="wallet-chip" aria-label="Credits used">
      <div className="wallet-chip-icon" aria-hidden="true">
        <svg viewBox="0 0 24 24" fill="none">
          <path
            d="M4 7.5A2.5 2.5 0 0 1 6.5 5h10A2.5 2.5 0 0 1 19 7.5V9h-2.75A3.25 3.25 0 0 0 13 12.25v.5A3.25 3.25 0 0 0 16.25 16H19v.5A2.5 2.5 0 0 1 16.5 19h-10A2.5 2.5 0 0 1 4 16.5v-9Z"
            stroke="currentColor"
            strokeWidth="1.5"
          />
          <path
            d="M14 12.25A1.75 1.75 0 0 1 15.75 10.5H20v4h-4.25A1.75 1.75 0 0 1 14 12.75v-.5Z"
            stroke="currentColor"
            strokeWidth="1.5"
          />
          <circle cx="16.75" cy="12.5" r=".75" fill="currentColor" />
        </svg>
      </div>
      <p className="wallet-chip-inline">
        <span className="wallet-chip-inline-label">Credits used:</span>{" "}
        <span className="wallet-chip-inline-value">{creditsUsed}</span>
      </p>
    </div>
  );
}

function WorkspaceMobileActions({ creditsUsed, onLogout }) {
  return (
    <nav className="workspace-mobile-actions" aria-label="Workspace actions">
      <Link to="/workspace/sites/new" className="workspace-mobile-action workspace-mobile-action-primary">
        <svg aria-hidden="true" viewBox="0 0 24 24" fill="none">
          <path d="M12 5v14" />
          <path d="M5 12h14" />
        </svg>
        <span>Add facility</span>
      </Link>
      <Link to="/workspace/credits" className="workspace-mobile-action">
        <svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M4 7.5A2.5 2.5 0 0 1 6.5 5h10A2.5 2.5 0 0 1 19 7.5V9h-2.75A3.25 3.25 0 0 0 13 12.25v.5A3.25 3.25 0 0 0 16.25 16H19v.5A2.5 2.5 0 0 1 16.5 19h-10A2.5 2.5 0 0 1 4 16.5v-9Z" />
          <path d="M14 12.25A1.75 1.75 0 0 1 15.75 10.5H20v4h-4.25A1.75 1.75 0 0 1 14 12.75v-.5Z" />
        </svg>
        <span>Credits</span>
      </Link>
      <Link to="/workspace/billing" className="workspace-mobile-action">
        <svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
          <path d="M14 2v6h6" />
          <path d="M16 13H8" />
          <path d="M16 17H8" />
          <path d="M10 9H8" />
        </svg>
        <span>Billing</span>
      </Link>
      <Link to="/workspace/wishlist" className="workspace-mobile-action">
        <svg aria-hidden="true" viewBox="0 0 24 24" fill="none">
          <path d="M5.5 4.5h5.5a2 2 0 0 1 2 2v13a2.8 2.8 0 0 0-2-.85H5.5z" />
          <path d="M18.5 4.5H13a2 2 0 0 0-2 2v13a2.8 2.8 0 0 1 2-.85h5.5z" />
        </svg>
        <span>Wishlist</span>
      </Link>
      <div className="workspace-mobile-action workspace-mobile-credits" aria-label="Credits used">
        <svg aria-hidden="true" viewBox="0 0 24 24" fill="none">
          <path d="M4 7.5A2.5 2.5 0 0 1 6.5 5h10A2.5 2.5 0 0 1 19 7.5V9h-2.75A3.25 3.25 0 0 0 13 12.25v.5A3.25 3.25 0 0 0 16.25 16H19v.5A2.5 2.5 0 0 1 16.5 19h-10A2.5 2.5 0 0 1 4 16.5v-9Z" />
          <path d="M14 12.25A1.75 1.75 0 0 1 15.75 10.5H20v4h-4.25A1.75 1.75 0 0 1 14 12.75v-.5Z" />
        </svg>
        <span>Credits used: {creditsUsed}</span>
      </div>
      <button type="button" className="workspace-mobile-action" onClick={onLogout}>
        <svg aria-hidden="true" viewBox="0 0 24 24" fill="none">
          <path d="M10 6H6.5v12H10" />
          <path d="M14 8l4 4-4 4" />
          <path d="M18 12H9" />
        </svg>
        <span>Logout</span>
      </button>
    </nav>
  );
}

function WishlistRow({ item }) {
  const address = wishlistItemAddress(item);
  const mapsUrl = wishlistItemMapsUrl(item);
  const editDraft = {
    ...item,
    ...recommendationAddFacilityDraft(item),
    org_name: wishlistItemTitle(item),
    company_name: wishlistItemTitle(item),
    full_address: address,
  };
  return (
    <article className="site-bar-item wishlist-bar-item">
      <div className="site-bar-copy">
        <p className="site-bar-title">{wishlistItemTitle(item)}</p>
        {address && mapsUrl ? (
          <a className="site-bar-address address-link" href={mapsUrl} target="_blank" rel="noopener noreferrer">
            {address}
          </a>
        ) : (
          <p className="site-bar-address">{address}</p>
        )}
      </div>
      <div className="site-bar-actions wishlist-bar-actions">
        <div className="site-bar-action-row">
          <Link
            className="site-bar-link site-bar-link-primary"
            to="/workspace/sites/new"
            state={{ editDraft }}
          >
            Request pre-assessment
          </Link>
        </div>
      </div>
    </article>
  );
}

function WorkspaceWishlistPanel({ wishlist }) {
  return (
    <section className="workspace-wishlist-panel" aria-label="Wishlist">
      {wishlist.length ? (
        <div className="site-bar-list">
          {wishlist.map((item) => (
            <WishlistRow key={item.customer_context_id || item.site_id} item={item} />
          ))}
        </div>
      ) : (
        <div className="workspace-empty-state workspace-wishlist-empty">
          <h3>No wishlist sites yet</h3>
          <p>Add recommendations to wishlist from a report, then request a pre-assessment from here.</p>
        </div>
      )}
    </section>
  );
}

function SiteRow({ site }) {
  const [session] = useRequireSession();
  const [showShareDialog, setShowShareDialog] = useState(false);
  const title = site.company_name || "Saved site";
  const routeState = buildPreAssessmentRouteState(site);
  const notesRouteState = { ...routeState, activeTab: "notes" };
  const recommendationsRouteState = { ...routeState, activeTab: "recommendations" };
  const reportReady = Boolean(site.is_report_ready);
  return (
    <article className="site-bar-item">
      <div className="site-bar-copy">
        <p className="site-bar-title">{title}</p>
        <p className="site-bar-address">{site.full_address || ""}</p>
      </div>
      <div className="site-bar-actions">
        <div className="site-bar-action-row">
          <Link
            className="site-bar-link site-bar-link-primary"
            to={buildWorkspaceReportPath(routeState)}
            state={routeState}
            onClick={() => saveReportContext(routeState)}
          >
            View Report
          </Link>
          <Link
            className="site-bar-link site-bar-link-secondary"
            to={buildWorkspaceReportPath(notesRouteState)}
            state={notesRouteState}
            onClick={() => saveReportContext(notesRouteState)}
          >
            Notes
          </Link>
          <Link
            className="site-bar-link site-bar-link-secondary"
            to={buildWorkspaceReportPath(recommendationsRouteState)}
            state={recommendationsRouteState}
            onClick={() => saveReportContext(recommendationsRouteState)}
          >
            Recommendations
          </Link>
          {reportReady && (
            <button
              type="button"
              className="site-bar-link site-bar-link-secondary site-bar-share-button"
              onClick={() => setShowShareDialog(true)}
            >
              <svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <circle cx="18" cy="5" r="3" />
                <circle cx="6" cy="12" r="3" />
                <circle cx="18" cy="19" r="3" />
                <line x1="8.59" y1="13.51" x2="15.42" y2="17.49" />
                <line x1="15.41" y1="6.51" x2="8.59" y2="10.49" />
              </svg>
              Share
            </button>
          )}
        </div>
        <p className="site-bar-meta">
          {reportReady
            ? "Report available"
            : site.customer_site_metadata?.last_pre_assessment_requested_at
              ? `Requested ${formatDateTime(site.customer_site_metadata.last_pre_assessment_requested_at)}`
              : "Job not started yet"}
        </p>
      </div>
      {showShareDialog && reportReady ? (
        <ShareReportDialog
          report={{
            customer_site_id: site.customer_site_id,
            company_name: site.company_name,
          }}
          senderEmail={session?.email || ""}
          onClose={() => setShowShareDialog(false)}
        />
      ) : null}
    </article>
  );
}

function PinnedSampleReportRow() {
  return (
    <article className="site-bar-item sample-report-pinned-item">
      <div className="site-bar-copy">
        <p className="site-bar-title sample-report-pinned-title">
          <svg className="sample-report-pin-icon" aria-hidden="true" viewBox="0 0 24 24" fill="none">
            <path d="M15 4.5 19.5 9" />
            <path d="m14 5.5-5 5-3.5-.5L4 11.5l8.5 8.5 1.5-1.5-.5-3.5 5-5" />
            <path d="m9 15-5 5" />
          </svg>
          <span>BR Williams Pre-Assessment Sample</span>
        </p>
        <p className="site-bar-address">1535 Hillyer Robinson Parkway, Anniston, Alabama, USA</p>
      </div>
      <div className="site-bar-actions">
        <div className="site-bar-action-row">
          <Link
            className="site-bar-link site-bar-link-primary"
            to="/sample-reports/br-williams"
            state={{ returnToWorkspace: true }}
          >
            View Report
          </Link>
        </div>
      </div>
    </article>
  );
}

function buildPreAssessmentRouteState(siteOrPayload) {
  return {
    accountId:
      siteOrPayload?.account_id ||
      siteOrPayload?.active_account_id ||
      siteOrPayload?.accountId ||
      "",
    siteId: siteOrPayload?.site_id || siteOrPayload?.siteId || "",
    customerSiteId: siteOrPayload?.customer_site_id || siteOrPayload?.customerSiteId || "",
  };
}

function findWorkspaceSite(sites, { siteId = "", customerSiteId = "" } = {}) {
  if (!Array.isArray(sites) || !sites.length) return null;
  if (customerSiteId) {
    const match = sites.find((site) => site.customer_site_id === customerSiteId);
    if (match) return match;
  }
  if (!siteId) return null;
  const matches = sites.filter((site) => site.site_id === siteId);
  if (!matches.length) return null;
  if (matches.length === 1) return matches[0];
  return (
    matches.find((site) => site.is_report_ready && site.assigned_via === "shared_site") ||
    matches.find((site) => site.is_report_ready) ||
    matches[0]
  );
}

function buildWorkspaceReportPath(siteOrPayload) {
  return "/workspace/report";
}

function buildPendingSiteFromInput(form, sitePayload) {
  const validationState = form.validationState || {};
  const selectedCandidate = validationState.selectedCandidate || null;
  const candidateDomain = normalizeCandidateDomain(selectedCandidate);
  const companyName = selectedCandidate?.name || form.org_name;
  const domain = candidateDomain || form.org_domain;
  const address = selectedCandidate?.address || sitePayload.full_address || sitePayload.fullAddress || "";
  return {
    account_id: "",
    site_id: "",
    company_name: companyName,
    org_name: companyName,
    org_domain: domain,
    full_address: address,
    street: sitePayload.street || "",
    city: sitePayload.city || "",
    state: sitePayload.state || "",
    zip: sitePayload.zip || "",
    country: sitePayload.country || "US",
    place_id: selectedCandidate?.place_id || sitePayload.place_id || "",
    lat: sitePayload.lat ?? sitePayload.latitude ?? null,
    lng: sitePayload.lng ?? sitePayload.longitude ?? null,
    metadata: {
      site_name: companyName,
      site_type: "Pending pre-assessment site",
    },
    address_validation: validationState.validation
      ? {
          ...validationState.validation,
          request_basis: validationState.requestBasis,
          ...(selectedCandidate ? { selected_candidate: selectedCandidate } : {}),
        }
      : null,
    selected_candidate: selectedCandidate,
    justification: validationState.justification || "",
    request_basis: validationState.requestBasis || "",
  };
}

function AppNav({ backToHome = false }) {
  const [drawerOpen, setDrawerOpen] = useState(false);
  return (
    <>
      <nav role="navigation" aria-label="Main navigation">
        <div className="nav-logo">
          <img src="/images/Logo_Dark.png" alt="Hrytos" className="nav-logo-img" />
          <div className="nav-logo-stack">
            <span className="nav-logo-text">
              Automati<span>SOR</span>
            </span>
          </div>
        </div>

        {backToHome ? null : (
          <>
            <ul className="nav-links">
              <li>
                <a href="#how-it-works">Technology guide</a>
              </li>
              <li>
                <a href="#community">Community</a>
              </li>
              <li>
                <a href="https://www.hrytos.com/">For Solution Providers</a>
              </li>
            </ul>
            <div className="nav-actions">
              <Link to="/auth" className="nav-cta">
                Signup
              </Link>
            </div>
            <button
              className="nav-hamburger"
              type="button"
              aria-label="Open menu"
              aria-expanded={drawerOpen}
              aria-controls="nav-drawer"
              onClick={() => setDrawerOpen((value) => !value)}
            >
              <span></span>
              <span></span>
              <span></span>
            </button>
          </>
        )}
      </nav>

      {!backToHome ? (
        <nav
          id="nav-drawer"
          className={`nav-drawer ${drawerOpen ? "open" : ""}`}
          aria-label="Mobile navigation"
        >
          <a href="#how-it-works" onClick={() => setDrawerOpen(false)}>
            Technology guide
          </a>
          <a href="#community" onClick={() => setDrawerOpen(false)}>
            Community
          </a>
          <a href="https://www.hrytos.com/" onClick={() => setDrawerOpen(false)}>
            For Solution Providers
          </a>
          <Link to="/auth" className="drawer-cta" onClick={() => setDrawerOpen(false)}>
            Signup
          </Link>
        </nav>
      ) : null}
    </>
  );
}

function HomePage() {
  const [step, setStep] = useState(3);
  const totalSteps = 15;
  const progressPercent = (step / totalSteps) * 100;
  return (
    <>
      <AppNav />
      <section className="hero-wrapper">
        <div className="hero">
          <div className="hero-left">
            <div className="hero-eyebrow">Independent warehouse automation advisor</div>
            <h1>
              Find out what your facility <em>actually</em> needs to automate
            </h1>
            <p className="hero-body">
              Create an AutomatiSOR account with your work email and start running automation
              intelligence workflows with tracked monthly credit usage.
            </p>
            <div className="hero-actions">
              <Link to="/auth" className="btn-primary">
                Signup
                <svg
                  viewBox="0 0 14 14"
                  fill="none"
                  xmlns="http://www.w3.org/2000/svg"
                  aria-hidden="true"
                >
                  <path
                    d="M2 7h10M8 3l4 4-4 4"
                    stroke="white"
                    strokeWidth="1.5"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                  />
                </svg>
              </Link>
            </div>
            <div className="hero-trust">
              No vendor affiliation · No sales calls until you want them · Free to complete
            </div>
          </div>

          <div className="diag-card" role="region" aria-label="Automation profile preview">
            <div className="diag-card-header">Automation profile — preview</div>
            <div className="diag-question">
              <div className="diag-q-label">What is your facility size?</div>
              <div className="diag-options">
                <div className="diag-opt">Under 100K sq ft</div>
                <div className="diag-opt sel">100K – 300K sq ft</div>
                <div className="diag-opt">300K – 600K sq ft</div>
                <div className="diag-opt">600K – 1.5M sq ft</div>
              </div>
            </div>
            <div className="diag-question">
              <div className="diag-q-label">Your primary vertical</div>
              <div className="diag-options">
                <div className="diag-opt sel">3PL / public warehousing</div>
                <div className="diag-opt">E-commerce / D2C</div>
                <div className="diag-opt">In-line manufacturing</div>
                <div className="diag-opt">Retail distribution</div>
              </div>
            </div>
            <div className="diag-question">
              <div className="diag-q-label">Biggest pain right now</div>
              <div className="diag-options">
                <div className="diag-opt">Labour cost & shortage</div>
                <div className="diag-opt sel">Throughput ceiling</div>
                <div className="diag-opt">Space utilisation</div>
                <div className="diag-opt">Accuracy & errors</div>
              </div>
            </div>
            <div
              className="diag-progress"
              role="progressbar"
              aria-valuenow={step}
              aria-valuemin="0"
              aria-valuemax={totalSteps}
            >
              <div className="diag-progress-fill" style={{ width: `${progressPercent}%` }}></div>
            </div>
            <div className="diag-footer">
              <span className="diag-step-label">
                Question {step} of {totalSteps} · ~{Math.max(1, totalSteps - step)} min remaining
              </span>
              <button
                className="diag-next"
                type="button"
                onClick={() => setStep((value) => Math.min(totalSteps, value + 1))}
              >
                {step === totalSteps ? "See results" : "Continue"}
              </button>
            </div>
          </div>
        </div>
      </section>

      <section id="how-it-works" className="section">
        <div className="section-label">How it works</div>
        <h2>
          From pain to best-fit automation options in <em>five hours</em>
        </h2>
        <div className="how-grid">
          <div>
            <div className="how-step-num">01</div>
            <div className="how-step-title">Create your verified account</div>
            <div className="how-step-body">
              Start with your work email, verify the OTP, and set up your company and first site.
            </div>
          </div>
          <div>
            <div className="how-step-num">02</div>
            <div className="how-step-title">Add operating locations</div>
            <div className="how-step-body">
              Organize real sites under your company workspaces and prepare them for deeper
              workflows.
            </div>
          </div>
          <div id="community">
            <div className="how-step-num">03</div>
            <div className="how-step-title">Run pre-assessment workflows</div>
            <div className="how-step-body">
              Launch the next workflow for the right site and track usage for monthly invoicing.
            </div>
          </div>
        </div>
      </section>
    </>
  );
}

function TermsModal({ show, onClose }) {
  if (!show) return null;

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-content terms-modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <h3>Terms & Conditions</h3>
          <button
            type="button"
            className="modal-close-btn"
            onClick={onClose}
            aria-label="Close"
          >
            ✕
          </button>
        </div>
        <div className="modal-body">
          <p>
            Lorem ipsum dolor sit amet, consectetur adipiscing elit. Sed do eiusmod tempor incididunt 
            ut labore et dolore magna aliqua. Ut enim ad minim veniam, quis nostrud exercitation ullamco 
            laboris nisi ut aliquip ex ea commodo consequat.
          </p>
          <p>
            Duis aute irure dolor in reprehenderit in voluptate velit esse cillum dolore eu fugiat nulla 
            pariatur. Excepteur sint occaecat cupidatat non proident, sunt in culpa qui officia deserunt 
            mollit anim id est laborum.
          </p>
          <p>
            Sed ut perspiciatis unde omnis iste natus error sit voluptatem accusantium doloremque laudantium, 
            totam rem aperiam, eaque ipsa quae ab illo inventore veritatis et quasi architecto beatae vitae 
            dicta sunt explicabo.
          </p>
          <p>
            Nemo enim ipsam voluptatem quia voluptas sit aspernatur aut odit aut fugit, sed quia consequuntur 
            magni dolores eos qui ratione voluptatem sequi nesciunt. Neque porro quisquam est, qui dolorem 
            ipsum quia dolor sit amet, consectetur, adipisci velit.
          </p>
          <p>
            Ut enim ad minima veniam, quis nostrum exercitationem ullam corporis suscipit laboriosam, nisi 
            ut aliquid ex ea commodi consequatur? Quis autem vel eum iure reprehenderit qui in ea voluptate 
            velit esse quam nihil molestiae consequatur, vel illum qui dolorem eum fugiat quo voluptas nulla 
            pariatur.
          </p>
        </div>
      </div>
    </div>
  );
}

function PrivacyModal({ show, onClose }) {
  if (!show) return null;

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-content terms-modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <h3>Privacy Policy</h3>
          <button
            type="button"
            className="modal-close-btn"
            onClick={onClose}
            aria-label="Close"
          >
            ✕
          </button>
        </div>
        <div className="modal-body">
          <p>
            Lorem ipsum dolor sit amet, consectetur adipiscing elit. Sed do eiusmod tempor incididunt 
            ut labore et dolore magna aliqua. Ut enim ad minim veniam, quis nostrud exercitation ullamco 
            laboris nisi ut aliquip ex ea commodo consequat.
          </p>
          <p>
            Duis aute irure dolor in reprehenderit in voluptate velit esse cillum dolore eu fugiat nulla 
            pariatur. Excepteur sint occaecat cupidatat non proident, sunt in culpa qui officia deserunt 
            mollit anim id est laborum.
          </p>
          <p>
            Sed ut perspiciatis unde omnis iste natus error sit voluptatem accusantium doloremque laudantium, 
            totam rem aperiam, eaque ipsa quae ab illo inventore veritatis et quasi architecto beatae vitae 
            dicta sunt explicabo.
          </p>
          <p>
            Nemo enim ipsam voluptatem quia voluptas sit aspernatur aut odit aut fugit, sed quia consequuntur 
            magni dolores eos qui ratione voluptatem sequi nesciunt. Neque porro quisquam est, qui dolorem 
            ipsum quia dolor sit amet, consectetur, adipisci velit.
          </p>
          <p>
            Ut enim ad minima veniam, quis nostrum exercitationem ullam corporis suscipit laboriosam, nisi 
            ut aliquid ex ea commodi consequatur? Quis autem vel eum iure reprehenderit qui in ea voluptate 
            velit esse quam nihil molestiae consequatur, vel illum qui dolorem eum fugiat quo voluptas nulla 
            pariatur.
          </p>
        </div>
      </div>
    </div>
  );
}

function NewUserPage() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const shareToken = searchParams.get("share") || "";
  const addressPickerRef = useRef(null);
  const applyingOnboardingCandidateRef = useRef(false);
  const [stage, setStage] = useState("email");
  const [email, setEmail] = useState("");
  const [emailError, setEmailError] = useState("");
  const [formError, setFormError] = useState("");
  const [otp, setOtp] = useState("");
  const [loading, setLoading] = useState("");
  const [shareDetails, setShareDetails] = useState(null);
  const [sessionState, setSessionState] = useState({
    email: "",
    userMode: "new_user",
    nextStep: "email",
    authVerified: false,
    customerId: null,
    accountId: null,
    activeAccountId: null,
    companyName: "",
    companyDomain: "",
    creditsUsedTotal: 0,
    creditsUsedThisMonth: 0,
    accounts: [],
    sites: [],
  });
  const [onboarding, setOnboarding] = useState({
    first_name: "",
    last_name: "",
    customer_company_name: "",
    site_company_name: "",
    site_company_domain: "",
    hasAddress: false,
  });
  const [termsAccepted, setTermsAccepted] = useState(false);
  const [showTermsModal, setShowTermsModal] = useState(false);
  const [showPrivacyModal, setShowPrivacyModal] = useState(false);
  const [onboardingResolvedAddress, setOnboardingResolvedAddress] = useState(null);
  const [onboardingCandidateToConfirm, setOnboardingCandidateToConfirm] = useState(null);
  const [confirmingOnboardingCandidate, setConfirmingOnboardingCandidate] = useState(false);
  const onboardingValidation = useAutomaticAddressValidation({
    companyName: onboarding.site_company_name,
    domain: onboarding.site_company_domain,
    resolvedAddress: onboardingResolvedAddress,
  });
  const feedback = normalizeAuthFeedback(formError);

  // Handle share link resolution
  useEffect(() => {
    if (!shareToken) return;
    setLoading("share");
    fetchJson("/api/share/resolve", {
      method: "POST",
      body: JSON.stringify({ share_token: shareToken }),
    })
      .then((payload) => {
        setShareDetails(payload);
        setEmail(payload.recipient_email || "");
        setSessionState((current) => ({
          ...current,
          email: payload.recipient_email || "",
          nextStep: "email",
        }));
        setStage("email");
      })
      .catch((error) =>
        setFormError(error.message || "This share link is invalid. Continue with normal sign in.")
      )
      .finally(() => setLoading(""));
  }, [shareToken]);

  function resetOnboardingValidationOverrides() {
    onboardingValidation.resetOverrides();
  }

  function chooseOnboardingCandidate(candidate) {
    setOnboardingCandidateToConfirm(candidate);
  }

  async function confirmOnboardingCandidate() {
    const candidate = onboardingCandidateToConfirm;
    if (!candidate) return;
    const candidateDomain = normalizeCandidateDomain(candidate);
    const nextResolved = resolvedAddressFromCandidate(candidate, onboardingResolvedAddress || {});
    applyingOnboardingCandidateRef.current = true;
    setConfirmingOnboardingCandidate(true);
    try {
      setOnboarding((current) => ({
        ...current,
        site_company_name: candidate.name || current.site_company_name,
        site_company_domain: candidateDomain || current.site_company_domain,
        hasAddress: Boolean(nextResolved?.full_address),
      }));
      setOnboardingResolvedAddress(nextResolved || null);
      onboardingValidation.setSelectedCandidate(candidate);
      onboardingValidation.setJustification("");
      if (nextResolved) {
        const applied = await addressPickerRef.current?.applyResolvedAddress(nextResolved);
        if (applied?.full_address) {
          setOnboardingResolvedAddress(applied);
        }
      }
      setOnboardingCandidateToConfirm(null);
    } catch (error) {
      setFormError(error.message || "Could not apply the selected candidate.");
    } finally {
      setConfirmingOnboardingCandidate(false);
      applyingOnboardingCandidateRef.current = false;
    }
  }

  useEffect(() => {
    const saved = loadSession();
    if (!saved) return;
    setSessionState((current) => ({ ...current, ...saved }));
    setEmail(saved.email || "");
    if (saved.nextStep === "otp") {
      setStage("otp");
    } else if (saved.nextStep === "onboarding_step1" && saved.authVerified) {
      setStage("onboarding_step1");
      setOnboarding((current) => ({
        ...current,
        customer_company_name: saved.companyName || "",
      }));
    } else if (saved.nextStep === "onboarding_step2" && saved.authVerified) {
      setStage("onboarding_step2");
      setOnboarding((current) => ({
        ...current,
        customer_company_name: saved.companyName || "",
      }));
    } else if (saved.nextStep === "onboarding" && saved.authVerified) {
      // Legacy support for old "onboarding" stage
      setStage("onboarding_step1");
      setOnboarding((current) => ({
        ...current,
        customer_company_name: saved.companyName || "",
      }));
    } else {
      setStage("email");
    }
  }, []);

  function persistState(nextPatch) {
    const nextState = { ...sessionState, ...nextPatch };
    setSessionState(nextState);
    saveSession(nextState);
  }

  function showWorkspace(payload) {
    persistState(buildSessionFromPayload(sessionState, { ...payload, next_step: "workspace" }));
    navigate("/workspace");
  }

  const step1Ready = Boolean(
    onboarding.first_name &&
      onboarding.last_name &&
      onboarding.customer_company_name &&
      termsAccepted,
  );
  
  const step2Ready = Boolean(
    onboarding.site_company_name &&
      onboarding.site_company_domain &&
      onboarding.hasAddress &&
      onboardingValidation.canProceed,
  );
  
  const onboardingReady = Boolean(
    onboarding.first_name &&
      onboarding.last_name &&
      onboarding.customer_company_name &&
      onboarding.site_company_name &&
      onboarding.site_company_domain &&
      onboarding.hasAddress &&
      onboardingValidation.canProceed,
  );

  async function handleEmailSubmit(event) {
    event.preventDefault();
    setFormError("");
    const validation = workEmailError(email);
    if (validation) {
      setEmailError(validation);
      return;
    }
    setLoading("email");
    try {
      const checked = await fetchJson("/api/auth/check-email", {
        method: "POST",
        body: JSON.stringify({ email }),
      });
      const requested = await fetchJson("/api/auth/request-otp", {
        method: "POST",
        body: JSON.stringify({ email }),
      });
      const normalized = checked.email || normalizeEmail(email);
      persistState({
        email: normalized,
        userMode: checked.user_mode || requested.user_mode || "new_user",
        nextStep: "otp",
        authVerified: false,
      });
      setEmail(normalized);
      setStage("otp");
      setOtp("");
    } catch (error) {
      setFormError(error.message || "Could not continue.");
    } finally {
      setLoading("");
    }
  }

  async function verifyOtp() {
    setFormError("");
    if (!/^\d{6}$/.test(otp)) {
      setFormError("Enter the 6-digit OTP.");
      return;
    }
    setLoading("otp");
    try {
      const payload = await fetchJson("/api/auth/verify-otp", {
        method: "POST",
        body: JSON.stringify({ 
          email: sessionState.email || email, 
          otp,
          share_token: shareToken || undefined
        }),
      });
      
      // Handle share destination - navigate directly to shared report
      if (payload.share_destination) {
        const routeState = {
          accountId: payload.share_destination.account_id,
          siteId: payload.share_destination.site_id,
          customerSiteId: payload.share_destination.customer_site_id,
        };
        persistState(buildSessionFromPayload(sessionState, { ...payload, next_step: "workspace" }));
        saveReportContext(routeState);
        navigate("/workspace/report", { state: routeState });
        return;
      }
      
      if (payload.next_step === "workspace") {
        persistState({
          email: payload.email || sessionState.email || email,
          userMode: payload.user_mode || sessionState.userMode,
          nextStep: "workspace",
          authVerified: true,
        });
        showWorkspace(payload);
      } else if (payload.next_step === "onboarding_step1") {
        persistState({
          email: payload.email || sessionState.email || email,
          userMode: payload.user_mode || sessionState.userMode,
          nextStep: "onboarding_step1",
          authVerified: true,
        });
        setStage("onboarding_step1");
        if (payload.share_token) {
          setSessionState((current) => ({ ...current, shareToken: payload.share_token }));
        }
      } else if (payload.next_step === "onboarding_step2") {
        persistState({
          email: payload.email || sessionState.email || email,
          userMode: payload.user_mode || sessionState.userMode,
          nextStep: "onboarding_step2",
          authVerified: true,
        });
        setStage("onboarding_step2");
        setOnboarding((current) => ({
          ...current,
          customer_company_name: sessionState.companyName || "",
        }));
      } else {
        // Legacy support or fallback to step 1
        persistState({
          email: payload.email || sessionState.email || email,
          userMode: payload.user_mode || sessionState.userMode,
          nextStep: "onboarding_step1",
          authVerified: true,
        });
        setStage("onboarding_step1");
        setOnboarding((current) => ({
          ...current,
          customer_company_name: sessionState.companyName || "",
        }));
      }
    } catch (error) {
      setFormError(error.message || "Could not verify OTP.");
    } finally {
      setLoading("");
    }
  }

  async function resendOtp() {
    setFormError("");
    setLoading("resend");
    try {
      const payload = await fetchJson("/api/auth/request-otp", {
        method: "POST",
        body: JSON.stringify({ email: sessionState.email || email }),
      });
      persistState({ userMode: payload.user_mode || sessionState.userMode });
      setFormError("A fresh OTP has been sent.");
    } catch (error) {
      setFormError(error.message || "Could not resend OTP.");
    } finally {
      setLoading("");
    }
  }

  async function completeOnboardingStep1() {
    setFormError("");
    if (!onboarding.first_name || !onboarding.last_name || !onboarding.customer_company_name) {
      setFormError("All fields are required");
      return;
    }
    if (!termsAccepted) {
      setFormError("You must accept the Terms & Conditions");
      return;
    }

    setLoading("step1");
    try {
      const payload = await fetchJson("/api/onboarding/step1", {
        method: "POST",
        body: JSON.stringify({
          email: sessionState.email || email,
          first_name: onboarding.first_name,
          last_name: onboarding.last_name,
          customer_company_name: onboarding.customer_company_name,
          share_token: shareToken || undefined,
          terms_accepted: termsAccepted,
        }),
      });

      // Share flow: go directly to report
      if (payload.share_destination) {
        const routeState = {
          accountId: payload.share_destination.account_id,
          siteId: payload.share_destination.site_id,
          customerSiteId: payload.share_destination.customer_site_id,
        };
        persistState(buildSessionFromPayload(sessionState, payload));
        saveReportContext(routeState);
        navigate("/workspace/report", { state: routeState });
        return;
      }

      // Normal flow: proceed to step 2
      persistState(buildSessionFromPayload(sessionState, payload));
      setStage("onboarding_step2");
    } catch (error) {
      setFormError(error.message || "Could not complete step 1");
    } finally {
      setLoading("");
    }
  }

  async function completeOnboardingStep2() {
    setFormError("");
    setLoading("step2");
    try {
      const sitePayload = await addressPickerRef.current.resolveCurrentAddress();
      const payload = await fetchJson("/api/onboarding/complete", {
        method: "POST",
        body: JSON.stringify({
          email: sessionState.email || email,
        }),
      });
      const nextState = buildSessionFromPayload(sessionState, payload);
      persistState(nextState);
      navigate("/workspace/pre-assessment", {
        state: {
          pendingSite: buildPendingSiteFromInput(
            {
              org_name: onboarding.site_company_name,
              org_domain: onboarding.site_company_domain,
              validationState: onboardingValidation,
            },
            sitePayload,
          ),
        },
      });
    } catch (error) {
      setFormError(error.message || "Could not prepare the pre-assessment request.");
    } finally {
      setLoading("");
    }
  }

  // Render onboarding on separate screen like workspace
  if (stage === "onboarding_step1" || stage === "onboarding_step2") {
    return (
      <div className="signup-body">
        <AppNav backToHome />
        <main className="workspace-page-shell signup-body workspace-body">
          <section className="workspace-page onboarding-workspace-page">
            <section className="auth-stage-card auth-stage-card-wide">
              <div className="auth-stage-header">
                <h3>{shareDetails ? "Access shared report" : "Finish your onboarding"}</h3>
                <p>{shareDetails ? "Add your details to access the report" : "Complete these steps to create your workspace and request your first pre-assessment."}</p>
              </div>

              {/* Stepper - only show for normal users (not share recipients) */}
              {!shareDetails && (
                <div className="onboarding-stepper">
                  <div className={`stepper-step ${stage === "onboarding_step1" ? "stepper-step-active" : stage === "onboarding_step2" ? "stepper-step-completed" : ""}`}>
                    <div className="stepper-step-number">1</div>
                    <div className="stepper-step-label">Add your details</div>
                  </div>
                  <div className="stepper-line"></div>
                  <div className={`stepper-step ${stage === "onboarding_step2" ? "stepper-step-active" : ""}`}>
                    <div className="stepper-step-number">2</div>
                    <div className="stepper-step-label">Add your first facility</div>
                  </div>
                </div>
              )}

              {stage === "onboarding_step1" ? (
                <section className="onboarding-section">
                  <div className="onboarding-section-head">
                    <h4>Add your details</h4>
                  </div>
                  <div className="modern-form-grid">
                    {[
                      ["First name", "first_name"],
                      ["Last name", "last_name"],
                    ].map(([label, key]) => (
                      <label key={key} className="modern-field">
                        <span>{label}</span>
                        <input
                          value={onboarding[key]}
                          onChange={(event) =>
                            setOnboarding((current) => ({ ...current, [key]: event.target.value }))
                          }
                          placeholder={label}
                        />
                      </label>
                    ))}
                    <label className="modern-field modern-field-wide">
                      <span>Company name</span>
                      <input
                        value={onboarding.customer_company_name}
                        onChange={(event) =>
                          setOnboarding((current) => ({
                            ...current,
                            customer_company_name: event.target.value,
                          }))
                        }
                        placeholder="Company name"
                      />
                    </label>
                  </div>

                  <label className="terms-checkbox">
                    <input
                      type="checkbox"
                      checked={termsAccepted}
                      onChange={(e) => setTermsAccepted(e.target.checked)}
                    />
                    <span>
                      I agree to the{" "}
                      <button
                        type="button"
                        className="terms-link"
                        onClick={() => setShowTermsModal(true)}
                      >
                        Terms & Conditions
                      </button>
                      {" "}and{" "}
                      <button
                        type="button"
                        className="terms-link"
                        onClick={() => setShowPrivacyModal(true)}
                      >
                        Privacy Policy
                      </button>
                    </span>
                  </label>
                </section>
              ) : null}

              {stage === "onboarding_step2" ? (
                <section className="site-card-modern onboarding-section">
                  <div className="onboarding-section-head">
                    <h4>Add your first facility</h4>
                  </div>
                  <div className="modern-form-grid">
                    <label className="modern-field">
                      <span>Facility name</span>
                      <input
                        value={onboarding.site_company_name}
                        onChange={(event) => {
                          resetOnboardingValidationOverrides();
                          setOnboarding((current) => ({
                            ...current,
                            site_company_name: event.target.value,
                          }));
                        }}
                        placeholder="Facility name"
                      />
                    </label>
                    <label className="modern-field">
                      <span>Facility domain</span>
                      <input
                        value={onboarding.site_company_domain}
                        onChange={(event) => {
                          const selectedCandidateNeedsDomain =
                            onboardingValidation.selectedCandidate &&
                            !normalizeCandidateDomain(onboardingValidation.selectedCandidate) &&
                            !onboarding.site_company_domain;
                          if (!selectedCandidateNeedsDomain) {
                            resetOnboardingValidationOverrides();
                          }
                          setOnboarding((current) => ({
                            ...current,
                            site_company_domain: event.target.value,
                          }));
                        }}
                        placeholder="Facility domain"
                      />
                    </label>
                  </div>
                  <GoogleAddressPicker
                    ref={addressPickerRef}
                    inputId="onboardingSiteLocationInput"
                    googleButtonId="onboardingOpenInGoogleMapsBtn"
                    mapId="onboardingSiteMap"
                    messageId="onboardingSiteMessage"
                    mapLabel="Interactive site map for onboarding"
                    addressLabel="Facility address"
                    addressPlaceholder="Start typing a facility address"
                    onResolvedChange={({ resolvedAddress, inputValue }) => {
                      if (!applyingOnboardingCandidateRef.current) {
                        resetOnboardingValidationOverrides();
                      }
                      setOnboardingResolvedAddress(resolvedAddress || null);
                      setOnboarding((current) => ({
                        ...current,
                        hasAddress: Boolean((resolvedAddress || {}).full_address),
                      }));
                    }}
                  />
                  <AddressValidationPanel
                    validation={onboardingValidation.validation}
                    validationError={onboardingValidation.validationError}
                    checking={onboardingValidation.checking}
                    selectedCandidate={onboardingValidation.selectedCandidate}
                    onSelectCandidate={chooseOnboardingCandidate}
                    justification={onboardingValidation.justification}
                    onJustificationChange={onboardingValidation.setJustification}
                    hasValidationInputs={onboardingValidation.hasValidationInputs}
                    domain={onboarding.site_company_domain}
                    addressLabel="Facility address"
                  />
                  <CandidateConfirmationModal
                    candidate={onboardingCandidateToConfirm}
                    loading={confirmingOnboardingCandidate}
                    onCancel={() => setOnboardingCandidateToConfirm(null)}
                    onConfirm={confirmOnboardingCandidate}
                    addressLabel="Facility address"
                  />
                </section>
              ) : null}

              <div className="auth-primary-action">
                <button
                  type="button"
                  className="btn-primary btn-submit-wide"
                  onClick={stage === "onboarding_step1" ? completeOnboardingStep1 : completeOnboardingStep2}
                  disabled={stage === "onboarding_step1" ? (!step1Ready || loading === "step1") : (!step2Ready || loading === "step2")}
                >
                  {loading === "step1" || loading === "step2" ? "Please wait..." : stage === "onboarding_step1" ? (shareDetails ? "Access Report" : "Next") : "Request pre-assessment"}
                </button>
              </div>

              <p className={`form-error ${formError ? "" : "hidden"}`}>{formError}</p>
            </section>
          </section>
        </main>
        <TermsModal show={showTermsModal} onClose={() => setShowTermsModal(false)} />
        <PrivacyModal show={showPrivacyModal} onClose={() => setShowPrivacyModal(false)} />
      </div>
    );
  }

  return (
    <div className="signup-body">
      <AppNav backToHome />
      <main className="signup-page signup-page-modern">
        <section className="auth-shell-modern auth-shell-with-explainer">
          <AuthExplainerPanel />
          <section id="signupShell" className="auth-panel-modern">
            <div className="auth-panel-head">
              <h2 id="signupPageTitle">
                {stage === "email"
                  ? "Start with your work email"
                  : stage === "otp"
                    ? "Verify your email"
                    : "Create your account"}
              </h2>
              <p id="signupIntro" className="signup-subtitle auth-panel-copy">
                {stage === "email"
                  ? ""
                  : stage === "otp"
                    ? "Use the one-time password to continue into your workspace."
                    : "Account does not exist. Finish the onboarding to create your workspace."}
              </p>
            </div>

            <div className={`auth-feedback auth-feedback-${feedback.tone} ${formError ? "" : "hidden"}`}>
              <p className="auth-feedback-title">{feedback.title}</p>
              <p className="auth-feedback-message">{feedback.message}</p>
            </div>

            {stage === "email" ? (
              <section className="auth-stage-card">
                <div className="auth-stage-header">
                  <h3>Enter your work email</h3>
                  <p>
                    {shareDetails
                      ? "Sign in with the invited email to view the report."
                      : "Use a company email address. Personal inboxes are blocked."}
                  </p>
                </div>
                <form onSubmit={handleEmailSubmit} noValidate>
                  <label className="modern-field">
                    <span>Work email</span>
                    <input
                      id="work_email"
                      type="email"
                      autoComplete="email"
                      placeholder="you@company.com"
                      value={email}
                      readOnly={Boolean(shareDetails)}
                      onChange={(event) => {
                        setEmail(event.target.value);
                        setEmailError("");
                      }}
                    />
                  </label>
                  <p className={`form-error ${emailError ? "" : "hidden"}`}>{emailError}</p>
                  <div className="auth-primary-action">
                    <button
                      type="submit"
                      className="btn-primary btn-submit-wide"
                      disabled={loading === "email"}
                    >
                      {loading === "email" ? "Please wait..." : "Continue"}
                    </button>
                  </div>
                </form>
              </section>
            ) : null}

            {stage === "otp" ? (
              <section className="auth-stage-card">
                <div className="auth-stage-header">
                  <h3>Verify your email</h3>
                  <p>
                    Enter the 6-digit code sent to <strong>{sessionState.email || email}</strong>.
                  </p>
                </div>
                <div className="auth-inline-grid">
                  <label className="modern-field">
                    <span>One-time password</span>
                    <input
                      inputMode="numeric"
                      autoComplete="one-time-code"
                      maxLength={6}
                      placeholder="6-digit OTP"
                      value={otp}
                      onChange={(event) => setOtp(event.target.value.replace(/\D/g, "").slice(0, 6))}
                    />
                  </label>
                  <button
                    type="button"
                    className="btn-primary auth-inline-cta"
                    onClick={verifyOtp}
                    disabled={loading === "otp"}
                  >
                    {loading === "otp" ? "Please wait..." : "Verify OTP"}
                  </button>
                </div>
                <div className="auth-secondary-actions">
                  <button
                    type="button"
                    className="btn-secondary"
                    onClick={resendOtp}
                    disabled={loading === "resend"}
                  >
                    {loading === "resend" ? "Please wait..." : "Resend OTP"}
                  </button>
                  <button
                    type="button"
                    className="auth-link-btn"
                    onClick={() => {
                      setStage("email");
                      persistState({ nextStep: "email", authVerified: false });
                      setOtp("");
                    }}
                  >
                    Use a different email
                  </button>
                </div>
              </section>
            ) : null}
          </section>
        </section>
      </main>
      <TermsModal show={showTermsModal} onClose={() => setShowTermsModal(false)} />
      <PrivacyModal show={showPrivacyModal} onClose={() => setShowPrivacyModal(false)} />
    </div>
  );
}

function useRequireSession() {
  const navigate = useNavigate();
  const [session, setSession] = useState(() => loadSession());
  useEffect(() => {
    if (!session || !session.email) {
      navigate("/auth", { replace: true });
    }
  }, [navigate, session]);
  return [session, setSession];
}

function WorkspaceLayout() {
  const navigate = useNavigate();
  const [session, setSession] = useRequireSession();

  // Keep the chip in sync whenever any child page calls saveSession
  useEffect(() => {
    function onSessionUpdated(e) {
      if (e.detail?.creditsUsedTotal !== undefined) {
        setSession((prev) => ({ ...prev, creditsUsedTotal: e.detail.creditsUsedTotal }));
      }
    }
    window.addEventListener("sessionUpdated", onSessionUpdated);
    return () => window.removeEventListener("sessionUpdated", onSessionUpdated);
  }, []);

  async function logout() {
    try {
      await fetchJson("/api/auth/logout", { method: "POST", body: JSON.stringify({}) });
    } catch {
      // Ignore logout failure.
    }
    clearSession();
    navigate("/auth");
  }

  if (!session?.email) return null;

  return (
    <>
      <div className="workspace-sticky-bar">
        <div className="workspace-sticky-bar-inner">
          <Link to="/workspace/sites/new" className="btn-primary workspace-sticky-link workspace-sticky-link-add">
            Add new facility
          </Link>
          <Link to="/workspace" className="btn-secondary workspace-sticky-link workspace-sticky-link-workspace">
            Facilities
          </Link>
          <Link to="/workspace/wishlist" className="btn-secondary workspace-sticky-link workspace-sticky-link-wishlist">
            Wishlist
          </Link>
          <Link to="/workspace/credits" className="btn-secondary workspace-sticky-link workspace-sticky-link-credits">
            Credits
          </Link>
          <Link to="/workspace/billing" className="btn-secondary workspace-sticky-link workspace-sticky-link-billing">
            Billing
          </Link>
          <CreditsUsedChip creditsUsed={session?.creditsUsedTotal || 0} />
          <ProfileMenu session={session} onLogout={logout} />
        </div>
      </div>
      <Outlet context={{ onLogout: logout }} />
    </>
  );
}

function WorkspacePage() {
  const [session, setSession] = useRequireSession();
  const { onLogout = () => {} } = useOutletContext() || {};
  const [error, setError] = useState("");
  const [workspace, setWorkspace] = useState(() => session || loadSession());
  const [loadingWorkspace, setLoadingWorkspace] = useState(false);
  const [activeTab, setActiveTab] = useState("my");

  useEffect(() => {
    if (!session?.email) return;
    setLoadingWorkspace(true);
    fetchJson("/api/workspace/state", {
      method: "POST",
      body: JSON.stringify({
        email: session.email,
        active_account_id: session.activeAccountId || session.accountId || "",
      }),
    })
      .then((payload) => {
        const nextState = buildSessionFromPayload(session, payload);
        saveSession(nextState);
        setSession(nextState);
        setWorkspace(nextState);
      })
      .catch((nextError) => setError(nextError.message || "Could not load the workspace."))
      .finally(() => setLoadingWorkspace(false));
  }, [session?.email]);

  if (!session?.email) return null;

  const allSites = workspace?.sites || [];
  const wishlist = workspace?.wishlist || [];
  const myFacilities = allSites.filter((site) => site.assigned_via !== "shared_site");
  const sharedFacilities = allSites.filter((site) => site.assigned_via === "shared_site");
  return (
    <main className="workspace-page-shell signup-body workspace-body">
      <section className="workspace-page">
        <WorkspaceMobileActions
          creditsUsed={workspace?.creditsUsedTotal || 0}
          onLogout={onLogout}
        />

        <p className={`form-error ${error ? "" : "hidden"}`}>{error}</p>

        {/* Facilities Tabs */}
        <div className="tab-row workspace-facilities-tabs" role="tablist" aria-label="Facilities">
          <button
            type="button"
            className={`tab-btn ${activeTab === "my" ? "tab-btn-active" : ""}`}
            onClick={() => setActiveTab("my")}
            role="tab"
            aria-selected={activeTab === "my"}
          >
            My facilities
          </button>
          <button
            type="button"
            className={`tab-btn ${activeTab === "shared" ? "tab-btn-active" : ""}`}
            onClick={() => setActiveTab("shared")}
            role="tab"
            aria-selected={activeTab === "shared"}
          >
            Shared facilities
          </button>
        </div>

        <section className="workspace-sites-panel">
          {loadingWorkspace && !allSites.length ? (
            <div className="workspace-loading-state">
              <p>Loading saved sites...</p>
            </div>
          ) : (
            <>
              {/* My Facilities Tab */}
              {activeTab === "my" && (
                <div className="site-bar-list">
                  <PinnedSampleReportRow />
                  {myFacilities.map((site) => (
                    <SiteRow key={site.customer_site_id} site={site} />
                  ))}
                  {!myFacilities.length && (
                    <div className="workspace-empty-state">
                      <h3>No facilities added yet</h3>
                      <Link to="/workspace/sites/new" className="btn-primary">
                        Add first facility
                      </Link>
                    </div>
                  )}
                </div>
              )}
              
              {/* Shared Facilities Tab */}
              {activeTab === "shared" && (
                <div className="site-bar-list">
                  {sharedFacilities.length > 0 ? (
                    sharedFacilities.map((site) => (
                      <SiteRow key={site.customer_site_id} site={site} />
                    ))
                  ) : (
                    <div className="workspace-empty-state">
                      <h3>No shared facilities yet</h3>
                      <p>Reports shared with you by other users will appear here.</p>
                    </div>
                  )}
                </div>
              )}
            </>
          )}
        </section>
      </section>
    </main>
  );
}

function WishlistPage() {
  const [session, setSession] = useRequireSession();
  const { onLogout = () => {} } = useOutletContext() || {};
  const [error, setError] = useState("");
  const [workspace, setWorkspace] = useState(() => session || loadSession());
  const [loadingWorkspace, setLoadingWorkspace] = useState(false);

  useEffect(() => {
    if (!session?.email) return;
    setLoadingWorkspace(true);
    fetchJson("/api/workspace/state", {
      method: "POST",
      body: JSON.stringify({
        email: session.email,
        active_account_id: session.activeAccountId || session.accountId || "",
      }),
    })
      .then((payload) => {
        const nextState = buildSessionFromPayload(session, payload);
        saveSession(nextState);
        setSession(nextState);
        setWorkspace(nextState);
      })
      .catch((nextError) => setError(nextError.message || "Could not load the wishlist."))
      .finally(() => setLoadingWorkspace(false));
  }, [session?.email]);

  if (!session?.email) return null;

  const wishlist = workspace?.wishlist || [];
  return (
    <main className="workspace-page-shell signup-body workspace-body">
      <section className="workspace-page">
        <header className="workspace-topbar workspace-topbar-titleonly">
          <div className="workspace-topbar-copy">
            <h1 className="workspace-page-title">Wishlist</h1>
          </div>
        </header>
        <WorkspaceMobileActions creditsUsed={workspace?.creditsUsedTotal || 0} onLogout={onLogout} />

        <p className={`form-error ${error ? "" : "hidden"}`}>{error}</p>

        <section className="workspace-sites-panel">
          {loadingWorkspace && !wishlist.length ? (
            <div className="workspace-loading-state">
              <p>Loading wishlist...</p>
            </div>
          ) : (
            <WorkspaceWishlistPanel wishlist={wishlist} />
          )}
        </section>
      </section>
    </main>
  );
}

function NewSitePage() {
  const navigate = useNavigate();
  const location = useLocation();
  const pickerRef = useRef(null);
  const applyingSiteCandidateRef = useRef(false);
  const [session, setSession] = useRequireSession();
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [loading, setLoading] = useState(false);
  const [siteCandidateToConfirm, setSiteCandidateToConfirm] = useState(null);
  const [confirmingSiteCandidate, setConfirmingSiteCandidate] = useState(false);
  const editDraft = location.state?.editDraft || null;
  const initialAddress = normalizeResolvedAddress(editDraft);
  const skippingInitialAddressPublishRef = useRef(Boolean(initialAddress?.full_address));
  const [resolvedAddress, setResolvedAddress] = useState(initialAddress);
  const [form, setForm] = useState(() => ({
    org_name: editDraft?.org_name || editDraft?.company_name || "",
    org_domain: editDraft?.org_domain || "",
    hasAddress: Boolean(initialAddress?.full_address),
  }));
  const siteValidation = useAutomaticAddressValidation({
    companyName: form.org_name,
    domain: form.org_domain,
    resolvedAddress,
    initialSelectedCandidate: initialSelectedCandidateFromDraft(editDraft),
    initialJustification: initialJustificationFromDraft(editDraft),
  });

  function resetSiteValidationOverrides() {
    siteValidation.resetOverrides();
  }

  function chooseSiteCandidate(candidate) {
    setSiteCandidateToConfirm(candidate);
  }

  async function confirmSiteCandidate() {
    const candidate = siteCandidateToConfirm;
    if (!candidate) return;
    const candidateDomain = normalizeCandidateDomain(candidate);
    const nextResolved = resolvedAddressFromCandidate(candidate, resolvedAddress || {});
    applyingSiteCandidateRef.current = true;
    setConfirmingSiteCandidate(true);
    try {
      setForm((current) => ({
        ...current,
        org_name: candidate.name || current.org_name,
        org_domain: candidateDomain || current.org_domain,
        hasAddress: Boolean(nextResolved?.full_address),
      }));
      setResolvedAddress(nextResolved || null);
      siteValidation.setSelectedCandidate(candidate);
      siteValidation.setJustification("");
      if (nextResolved) {
        const applied = await pickerRef.current?.applyResolvedAddress(nextResolved);
        if (applied?.full_address) {
          setResolvedAddress(applied);
        }
      }
      setSiteCandidateToConfirm(null);
    } catch (error) {
      setMessage(error.message || "Could not apply the selected candidate.");
    } finally {
      setConfirmingSiteCandidate(false);
      applyingSiteCandidateRef.current = false;
    }
  }

  useEffect(() => {
    if (!session?.email) return;
    fetchJson("/api/workspace/state", {
      method: "POST",
      body: JSON.stringify({
        email: session.email,
        active_account_id: session.activeAccountId || session.accountId || "",
      }),
    })
      .then((payload) => {
        const nextState = buildSessionFromPayload(session, payload);
        saveSession(nextState);
        setSession(nextState);
      })
      .catch((nextError) => setError(nextError.message || "Could not load workspace accounts."));
  }, [session?.email]);

  const ready = Boolean(form.org_name && form.org_domain && form.hasAddress && siteValidation.canProceed);

  async function continueToPreAssessment() {
    setError("");
    setMessage("");
    setLoading(true);
    try {
      const sitePayload = await pickerRef.current.resolveCurrentAddress();
      navigate("/workspace/pre-assessment", {
        state: {
          pendingSite: buildPendingSiteFromInput(
            { ...form, validationState: siteValidation },
            sitePayload,
          ),
        },
      });
    } catch (nextError) {
      setMessage(nextError.message || "Could not prepare the pre-assessment request.");
    } finally {
      setLoading(false);
    }
  }

  if (!session?.email) return null;

  return (
    <main className="workspace-page-shell signup-body workspace-body">
      <section className="workspace-page workspace-form-page new-site-page">
        <header className="workspace-subpage-head">
          <div className="workspace-subpage-bar">
            <div>
              <p className="workspace-eyebrow">Workspace</p>
              <h1 className="workspace-page-title">Add a new facility</h1>
              <p className="workspace-page-copy">
                Enter the account details and operating location. We will save the site only after
                you confirm the pre-assessment request.
              </p>
            </div>
          </div>
        </header>

        <p className={`form-error ${error ? "" : "hidden"}`}>{error}</p>

        <section className="workspace-card workspace-card-modern workspace-card-form workspace-card-wide">
          <div className="workspace-form-grid">
            <label className="workspace-field">
              <span>Company name</span>
              <input
                value={form.org_name}
                onChange={(event) => {
                  resetSiteValidationOverrides();
                  setForm((current) => ({ ...current, org_name: event.target.value }));
                }}
                placeholder="Company name"
              />
            </label>
            <label className="workspace-field">
              <span>Company domain</span>
              <input
                value={form.org_domain}
                onChange={(event) => {
                  const selectedCandidateNeedsDomain =
                    siteValidation.selectedCandidate &&
                    !normalizeCandidateDomain(siteValidation.selectedCandidate) &&
                    !form.org_domain;
                  if (!selectedCandidateNeedsDomain) {
                    resetSiteValidationOverrides();
                  }
                  setForm((current) => ({ ...current, org_domain: event.target.value }));
                }}
                placeholder="company.com or https://company.com"
              />
            </label>
            <GoogleAddressPicker
              ref={pickerRef}
              inputId="siteLocationInput"
              googleButtonId="openInGoogleMapsBtn"
              mapId="siteMap"
              messageId="siteActionMessage"
              mapLabel="Satellite map for site selection"
              initialResolvedAddress={initialAddress}
              onResolvedChange={({ resolvedAddress, inputValue }) => {
                const isSameInitialAddress =
                  initialAddress?.full_address &&
                  resolvedAddress?.full_address === initialAddress.full_address;
                if (skippingInitialAddressPublishRef.current || isSameInitialAddress) {
                  skippingInitialAddressPublishRef.current = false;
                } else if (!applyingSiteCandidateRef.current) {
                  resetSiteValidationOverrides();
                }
                setResolvedAddress(resolvedAddress || null);
                setForm((current) => ({
                  ...current,
                  hasAddress: Boolean((resolvedAddress || {}).full_address),
                }));
              }}
            />
            <AddressValidationPanel
              validation={siteValidation.validation}
              validationError={siteValidation.validationError}
              checking={siteValidation.checking}
              selectedCandidate={siteValidation.selectedCandidate}
              onSelectCandidate={chooseSiteCandidate}
              justification={siteValidation.justification}
              onJustificationChange={siteValidation.setJustification}
              hasValidationInputs={siteValidation.hasValidationInputs}
              domain={form.org_domain}
            />
            <CandidateConfirmationModal
              candidate={siteCandidateToConfirm}
              loading={confirmingSiteCandidate}
              onCancel={() => setSiteCandidateToConfirm(null)}
              onConfirm={confirmSiteCandidate}
            />
          </div>

          <div className="workspace-map-toolbar workspace-map-toolbar-modern">
            <button type="button" className="btn-primary" onClick={continueToPreAssessment} disabled={!ready || loading}>
              {loading ? "Please wait..." : "Request pre-assessment"}
            </button>
          </div>

          <p className={`workspace-feedback ${message ? "" : "hidden"}`}>{message}</p>
        </section>
      </section>
    </main>
  );
}

function PreAssessmentPage() {
  const navigate = useNavigate();
  const location = useLocation();
  const [searchParams] = useSearchParams();
  const [session, setSession] = useRequireSession();
  const [error, setError] = useState("");
  const [reviewError, setReviewError] = useState("");
  const [loading, setLoading] = useState(false);
  const [reviewOpen, setReviewOpen] = useState(false);
  const [mode, setMode] = useState("flow");
  const [workspace, setWorkspace] = useState(() => session || loadSession());
  const [loadingWorkspace, setLoadingWorkspace] = useState(false);
  const [confirmedRouteState, setConfirmedRouteState] = useState(null);
  const [paymentRedirectSeconds, setPaymentRedirectSeconds] = useState(0);
  const paymentRedirectTimersRef = useRef([]);
  const preAssessmentSubmittingRef = useRef(false);
  const storedPreAssessmentContext = loadPreAssessmentContext();
  const queryRouteState = {
    accountId: searchParams.get("account_id") || "",
    siteId: searchParams.get("site_id") || "",
  };
  const routeState =
    location.state ||
    (queryRouteState.accountId || queryRouteState.siteId ? queryRouteState : null) ||
    storedPreAssessmentContext ||
    {};
  const pendingSite = routeState.pendingSite || null;
  const accountId = routeState.accountId || session?.activeAccountId || "";
  const siteId = routeState.siteId || "";

  useEffect(() => {
    if (routeState.accountId || routeState.siteId || routeState.pendingSite) {
      savePreAssessmentContext(routeState);
    }
    if (location.search) {
      navigate("/workspace/pre-assessment", { replace: true, state: routeState });
    }
  }, [location.search, navigate, routeState.accountId, routeState.siteId, routeState.pendingSite]);

  useEffect(() => {
    if (!session?.email) return;
    setLoadingWorkspace(true);
    fetchJson("/api/workspace/state", {
      method: "POST",
      body: JSON.stringify({
        email: session.email,
        active_account_id: accountId,
      }),
    })
      .then((payload) => {
        const nextState = buildSessionFromPayload(session, payload);
        saveSession(nextState);
        setSession(nextState);
        setWorkspace(nextState);
      })
      .catch((nextError) => setError(nextError.message || "Could not load pre-assessment details."))
      .finally(() => setLoadingWorkspace(false));
  }, [session?.email, accountId]);

  const selectedSite = useMemo(
    () =>
      pendingSite ||
      (workspace?.sites || []).find((site) => site.site_id === siteId) ||
      (workspace?.wishlist || []).find((site) => site.site_id === siteId) ||
      null,
    [workspace, siteId, pendingSite],
  );
  const isPendingSite = Boolean(pendingSite);
  const isResolvingSelectedSite = loadingWorkspace && Boolean(siteId) && !selectedSite;
  const preAssessmentPriceCredits =
    workspace?.preAssessmentPriceCredits ?? session?.preAssessmentPriceCredits ?? 2;
  const costLabel = `${preAssessmentPriceCredits} credit${preAssessmentPriceCredits === 1 ? "" : "s"}`;
  const paymentRedirecting = paymentRedirectSeconds > 0;

  function clearPaymentRedirectTimers() {
    paymentRedirectTimersRef.current.forEach((timerId) => window.clearTimeout(timerId));
    paymentRedirectTimersRef.current = [];
  }

  function resetPaymentRedirect() {
    clearPaymentRedirectTimers();
    setPaymentRedirectSeconds(0);
  }

  function startPaymentRedirect(message) {
    clearPaymentRedirectTimers();
    setPaymentRedirectSeconds(5);
    paymentRedirectTimersRef.current = [
      window.setTimeout(() => setPaymentRedirectSeconds(4), 1000),
      window.setTimeout(() => setPaymentRedirectSeconds(3), 2000),
      window.setTimeout(() => setPaymentRedirectSeconds(2), 3000),
      window.setTimeout(() => setPaymentRedirectSeconds(1), 4000),
      window.setTimeout(() => {
        navigate("/workspace/billing", { state: { message } });
      }, 5000),
    ];
  }

  useEffect(() => () => clearPaymentRedirectTimers(), []);

  function openReview() {
    if (!selectedSite) {
      setError("Select a site from the workspace before requesting a pre-assessment.");
      return;
    }
    setError("");
    resetPaymentRedirect();
    setReviewError("");
    setReviewOpen(true);
  }

  function closeReviewModal() {
    if (paymentRedirecting) return;
    setReviewOpen(false);
    setReviewError("");
  }

  function editSelectedSite() {
    if (paymentRedirecting) return;
    setReviewOpen(false);
    setReviewError("");
    if (isPendingSite) {
      navigate("/workspace/sites/new", {
        state: {
          editDraft: selectedSite,
        },
      });
      return;
    }
    navigate("/workspace");
  }

  async function requestPreAssessment() {
    if (paymentRedirecting || preAssessmentSubmittingRef.current) return;
    if (!selectedSite) {
      setError("Select a site from the workspace before requesting a pre-assessment.");
      return;
    }
    preAssessmentSubmittingRef.current = true;
    setError("");
    resetPaymentRedirect();
    setReviewError("");
    setLoading(true);
    try {
      let requestAccountId = accountId || selectedSite.account_id || "";
      let requestSiteId = selectedSite.site_id || "";
      let requestCustomerSiteId = selectedSite.customer_site_id || "";
      let justCreatedOwnedSite = false;
      let nextWorkspace = workspace;

      if (isPendingSite) {
        const savedSitePayload = await fetchJson("/api/account-sites", {
          method: "POST",
          body: JSON.stringify({
            email: session.email,
            org_name: pendingSite.org_name || pendingSite.company_name || "",
            org_domain: pendingSite.org_domain || "",
            full_address: pendingSite.full_address || "",
            street: pendingSite.street || "",
            city: pendingSite.city || "",
            state: pendingSite.state || "",
            zip: pendingSite.zip || "",
            country: pendingSite.country || "US",
            place_id: pendingSite.place_id || "",
            address_validation: pendingSite.address_validation || null,
            selected_candidate: pendingSite.selected_candidate || null,
            justification: pendingSite.justification || "",
            request_basis: pendingSite.request_basis || "",
          }),
        });

        nextWorkspace = buildSessionFromPayload(session, savedSitePayload);
        saveSession(nextWorkspace);
        setSession(nextWorkspace);
        setWorkspace(nextWorkspace);
        requestAccountId = savedSitePayload.account_id || nextWorkspace.activeAccountId || "";
        requestSiteId = savedSitePayload.site_id || "";
        requestCustomerSiteId = savedSitePayload.customer_site_id || "";
        justCreatedOwnedSite = savedSitePayload.site_status === "created";

        if (savedSitePayload.site_status === "already_exists" && savedSitePayload.can_proceed === false) {
          setReviewError(
            "You already added this facility and a pre-assessment is already in progress. Check your My facilities tab.",
          );
          return;
        }

        if (savedSitePayload.site_status !== "created" && savedSitePayload.site_status !== "already_exists") {
          throw new Error("Could not save the selected site before starting the pre-assessment.");
        }
      }

      if (!requestSiteId) {
        throw new Error("Could not save the selected site before starting the pre-assessment.");
      }

      const payload = await fetchJson("/api/pre-assessment/request", {
        method: "POST",
        body: JSON.stringify({
          email: session.email,
          account_id: requestAccountId,
          site_id: requestSiteId,
          customer_site_id: requestCustomerSiteId,
          confirmed: true,
        }),
      });

      if (
        justCreatedOwnedSite &&
        payload.message &&
        payload.message.toLowerCase().includes("already running")
      ) {
        setReviewError(
          "You already added this facility and a pre-assessment is already in progress. Check your My facilities tab.",
        );
        return;
      }

      const nextRouteState = {
        accountId: payload.account_id || requestAccountId,
        siteId: payload.site_id || requestSiteId,
      };
      const nextState = buildSessionFromPayload(session, {
        ...nextWorkspace,
        credits_used_total: payload.credits_used_total,
        credits_used_this_month: payload.credits_used_this_month,
      });
      saveSession(nextState);
      setSession(nextState);
      setWorkspace((current) => ({
        ...(current || nextState),
        creditsUsedTotal: nextState.creditsUsedTotal,
        creditsUsedThisMonth: nextState.creditsUsedThisMonth,
      }));
      saveReportContext(nextRouteState);
      clearPreAssessmentContext();
      navigate("/workspace/pre-assessment", { replace: true, state: nextRouteState });
      setConfirmedRouteState(nextRouteState);
      setReviewOpen(false);
      setMode("success");
    } catch (nextError) {
      const detail = nextError.payload?.detail;
      const errorCode = (typeof detail === "object" ? detail?.code : null) || nextError.code || "";
      const message =
        errorCode === "payment_method_required"
          ? "A payment method is required to add more sites. Please add a card on the Billing page."
          : (typeof detail === "object" ? detail?.message : null) || nextError.message || "Could not request the pre-assessment.";
      if (errorCode === "payment_method_required") {
        if (reviewOpen) {
          setReviewError(message);
        } else {
          setError(message);
        }
        startPaymentRedirect(message);
        return;
      }
      if (reviewOpen) {
        setReviewError(message);
      } else {
        setError(message);
      }
    } finally {
      setLoading(false);
      preAssessmentSubmittingRef.current = false;
    }
  }

  if (!session?.email) return null;

  return (
    <main className="workspace-page-shell signup-body workspace-body">
      <section className="workspace-page workspace-form-page pre-assessment-page">
        {mode === "flow" ? (
          <div id="preAssessmentFlow">
            <header className="workspace-subpage-head">
              <div className="workspace-subpage-bar">
                <div>
                  <p className="workspace-eyebrow">Workspace</p>
                  <h1 className="workspace-page-title">Request a pre-assessment</h1>
                  <p className="workspace-page-copy">
                    Review the selected site, understand the deliverable, and confirm the monthly
                    billed usage before the job starts.
                  </p>
                </div>
              </div>
            </header>

            <p className={`form-error ${error ? "" : "hidden"}`}>
              {error}
              {paymentRedirectSeconds ? ` Redirecting in ${paymentRedirectSeconds}...` : ""}
            </p>

            <section className="workspace-topbar-actions workspace-inline-stats">
              <div className="wallet-chip wallet-chip-muted">
                <p className="wallet-chip-inline">
                  <span className="wallet-chip-inline-label">Price:</span>{" "}
                  <span className="wallet-chip-inline-value">{costLabel}</span>
                </p>
              </div>
            </section>

            <section className="workspace-card workspace-card-modern workspace-card-wide pre-assessment-selection-card">
              {isResolvingSelectedSite ? (
                <div className="workspace-loading-state pre-assessment-loading-state">
                  <p>Loading selected site...</p>
                </div>
              ) : (
                <>
                  <h2 className="workspace-card-title pre-assessment-site-title">
                    {selectedSite?.company_name || "Choose a site from the workspace"}
                  </h2>
                  <p className="workspace-page-copy workspace-page-copy-tight">
                    {selectedSite?.full_address ||
                      "Open this page from a site row in the workspace so the request is tied to the correct company and address."}
                  </p>
                </>
              )}
            </section>

            <section className="workspace-card workspace-card-modern workspace-card-wide pre-assessment-card">
              <div className="tab-panel pre-assessment-meaning-panel">
                <h2 className="workspace-card-title">What is a site pre-assessment?</h2>
                <p className="workspace-copy">
                  A pre-assessment is a thorough, structured evaluation conducted prior to a formal audit or
                  operational review of a warehousing site. It serves as a foundational discovery process,
                  gathering critical information across all key dimensions of the operation to build a complete
                  picture of the site's current state.
                </p>

                <p className="workspace-copy">
                  At its core, a pre-assessment examines four primary areas:
                </p>

                <div className="pre-assessment-copy-stack">
                  <p className="workspace-copy">
                    <strong>Organization</strong> — This covers the background and profile of the business,
                    including its industry, customer base & service offerings. Understanding the business and what
                    it is trying to achieve provides the context needed to evaluate everything else.
                  </p>
                  <p className="workspace-copy">
                    <strong>Facility</strong> — A detailed look at the physical environment, including the size and
                    layout of the warehouse, the condition of the building and infrastructure, storage systems,
                    equipment, technology in use, and any health and safety considerations. This establishes what
                    the operation is working with in terms of physical capacity and capability.
                  </p>
                  <p className="workspace-copy">
                    <strong>Operations</strong> — An analysis of how the warehouse functions — inventory
                    management, order fulfillment processes, returns handling, and the use of warehouse management
                    systems. This reveals how efficiently and effectively the site is running relative to its goals
                    and industry benchmarks.
                  </p>
                  <p className="workspace-copy">
                    <strong>Labor</strong> — A review of the workforce supporting the operation, covering
                    headcount, shift structures, productivity levels, training practices, and workforce management.
                    Labor is often the largest cost driver in a warehouse, making this a critical area of scrutiny.
                  </p>
                </div>

                <p className="workspace-copy">
                  Taken together, a pre-assessment provides a holistic snapshot of a warehousing site before any
                  deeper engagement begins. It helps identify strengths, surface inefficiencies, flag risks, and
                  prioritize areas for improvement — ensuring that any subsequent recommendations or interventions
                  are grounded in a clear, accurate understanding of the operation as it actually exists.
                </p>

                <h2 className="workspace-card-title">How much does it cost?</h2>
                <div className="pre-assessment-cost-callout">
                  <p className="workspace-copy">
                    A site pre-assessment costs <strong>{costLabel}</strong>.
                  </p>
                </div>

                <h2 className="workspace-card-title">Sample site pre-assessment report</h2>
                <p className="workspace-copy">
                  Here is a sample of BR Williams, a 3PL at 1535 Hillyer Robinson Parkway, Anniston, Alabama
                </p>
                <div className="sample-report-link-row">
                  <Link
                    className="btn-secondary sample-report-link"
                    to="/sample-reports/br-williams"
                    state={{ returnToPreAssessment: routeState }}
                    onClick={() => savePreAssessmentContext(routeState)}
                  >
                    View Sample Report
                  </Link>
                </div>
              </div>

              <div className="auth-primary-action">
                <button
                  type="button"
                  className="btn-primary btn-glow"
                  onClick={openReview}
                  disabled={loading || !selectedSite}
                >
                  {loading ? "Please wait..." : "Confirm and start pre-assessment"}
                </button>
              </div>
            </section>

            {reviewOpen ? (
              <div className="review-modal-backdrop" role="presentation" onMouseDown={closeReviewModal}>
                <section
                  className="review-modal"
                  role="dialog"
                  aria-modal="true"
                  aria-labelledby="reviewModalTitle"
                  onMouseDown={(event) => event.stopPropagation()}
                >
                  <div className="review-modal-head">
                    <p className="workspace-eyebrow">Review</p>
                    <h2 id="reviewModalTitle" className="workspace-card-title">
                      Review
                    </h2>
                  </div>
                  <div className="pre-assessment-summary-grid review-summary-grid">
                    <div className="workspace-summary-chip">
                      <span className="workspace-summary-label">Company Name</span>
                      <span className="workspace-summary-value">{selectedSite?.company_name || "-"}</span>
                    </div>
                    <div className="workspace-summary-chip">
                      <span className="workspace-summary-label">Site Address</span>
                      <span className="workspace-summary-value">{selectedSite?.full_address || "-"}</span>
                    </div>
                    <div className="workspace-summary-chip">
                      <span className="workspace-summary-label">Cost of pre-assessment</span>
                      <span className="workspace-summary-value">{costLabel}</span>
                    </div>
                  </div>
                  <p className={`form-error ${reviewError ? "" : "hidden"}`}>
                    {reviewError}
                    {paymentRedirectSeconds ? ` Redirecting in ${paymentRedirectSeconds}...` : ""}
                  </p>
                  <div className="review-modal-actions">
                    <button
                      type="button"
                      className="btn-secondary"
                      onClick={editSelectedSite}
                      disabled={loading || paymentRedirecting}
                    >
                      Edit
                    </button>
                    <button
                      type="button"
                      className="btn-primary"
                      onClick={requestPreAssessment}
                      disabled={loading || paymentRedirecting}
                    >
                      {loading ? "Please wait..." : "Confirm and proceed"}
                    </button>
                  </div>
                </section>
              </div>
            ) : null}
          </div>
        ) : null}

        {mode === "success" ? (
          <section className="workspace-card workspace-card-modern workspace-card-wide thank-you-state">
            <div className="thank-you-icon" aria-hidden="true">
              ✓
            </div>
            <p className="workspace-eyebrow">Request confirmed</p>
            <h1 className="workspace-page-title">Thank you!</h1>
            <p className="workspace-page-copy">
              Your pre-assessment request is confirmed. The job is now running for this site, and
              we’ll email you as soon as the report is ready.
            </p>
            <div className="pre-assessment-summary-grid">
              <div className="workspace-summary-chip">
                <span className="workspace-summary-label">Company</span>
                <span className="workspace-summary-value">{selectedSite?.company_name || "-"}</span>
              </div>
              <div className="workspace-summary-chip">
                <span className="workspace-summary-label">Site address</span>
                <span className="workspace-summary-value">{selectedSite?.full_address || "-"}</span>
              </div>
            </div>
            <div className="auth-primary-action">
              <Link
                to="/workspace/report"
                className="btn-secondary"
                state={{ ...(confirmedRouteState || {}), activeTab: "notes" }}
                onClick={() => {
                  if (confirmedRouteState) saveReportContext(confirmedRouteState);
                }}
              >
                Notes
              </Link>
              <Link to="/workspace" className="btn-primary" onClick={clearPreAssessmentContext}>
                Back to workspace
              </Link>
            </div>
          </section>
        ) : null}

      </section>
    </main>
  );
}

function ReportRatingPanel({
  rating,
  savingField,
  savingFeedback,
  message,
  isError,
  onSelectRating,
  onFeedbackChange,
  onSaveFeedback,
}) {
  return (
    <section className="report-rating-panel" aria-label="Report feedback">
      <div className="report-rating-head">
        <p className="workspace-eyebrow">Report feedback</p>
        <h2 className="workspace-card-title">Rate this report</h2>
      </div>
      <div className="report-rating-grid">
        {REPORT_RATING_FIELDS.map((field) => (
          <div className="report-rating-field" key={field.key}>
            <div className="report-rating-copy">
              <span className="report-rating-label">{field.label}</span>
              <p>{field.question}</p>
            </div>
            <div className="report-rating-buttons" role="radiogroup" aria-label={field.label}>
              {REPORT_RATING_VALUES.map((value) => (
                <button
                  type="button"
                  key={value}
                  className={rating[field.key] >= value ? "report-rating-button active" : "report-rating-button"}
                  aria-checked={rating[field.key] === value}
                  aria-label={`${value} out of 5`}
                  role="radio"
                  onClick={() => onSelectRating(field.key, value)}
                  disabled={Boolean(savingField)}
                >
                  {savingField === field.key && rating[field.key] === value ? (
                    "..."
                  ) : (
                    <svg className="report-rating-star" aria-hidden="true" viewBox="0 0 24 24">
                      <path d="m12 3.5 2.6 5.3 5.9.9-4.2 4.1 1 5.8-5.3-2.8-5.3 2.8 1-5.8-4.2-4.1 5.9-.9L12 3.5Z" />
                    </svg>
                  )}
                </button>
              ))}
            </div>
          </div>
        ))}
      </div>
      <form className="report-rating-feedback" onSubmit={onSaveFeedback}>
        <label className="workspace-field report-rating-textarea">
          <span>Additional feedback</span>
          <textarea
            value={rating.additional_feedback}
            onChange={(event) => onFeedbackChange(event.target.value)}
            rows={3}
            placeholder="Share anything else about this report."
          />
        </label>
        <div className="report-notes-actions">
          <button type="submit" className="btn-primary" disabled={savingFeedback}>
            {savingFeedback ? "Saving..." : "Save feedback"}
          </button>
          <p className={`workspace-feedback ${message ? "" : "hidden"} ${isError ? "workspace-feedback-error" : ""}`}>
            {message}
          </p>
        </div>
      </form>
    </section>
  );
}

function RecommendationCard({ recommendation, fallbackCompanyName, wishlistedSiteIds, addingWishlistSiteId, onAddToWishlist }) {
  const navigate = useNavigate();
  const title = recommendationTitle(recommendation);
  const address = recommendationAddress(recommendation);
  const companyName = recommendationText(recommendation.company_name) || fallbackCompanyName;
  const siteType = recommendationText(recommendation.site_type);
  const reason = recommendationText(recommendation.reason);
  const mapsUrl = recommendationMapsUrl(recommendation);
  const siteId = recommendationText(recommendation.site_id);
  const isWishlisted = Boolean(siteId && wishlistedSiteIds.has(siteId));
  const isAddingWishlist = Boolean(siteId && addingWishlistSiteId === siteId);

  function addFacility() {
    navigate("/workspace/sites/new", {
      state: {
        editDraft: recommendationAddFacilityDraft(recommendation, fallbackCompanyName),
      },
    });
  }

  return (
    <article className="recommendation-card">
      <div className="recommendation-card-copy">
        {title ? <p className="recommendation-card-title">{title}</p> : null}
        {companyName && companyName !== title ? (
          <p className="recommendation-card-company">{companyName}</p>
        ) : null}
        {address && mapsUrl ? (
          <a className="recommendation-card-address recommendation-card-address-link" href={mapsUrl} target="_blank" rel="noopener noreferrer">
            {address}
          </a>
        ) : address ? (
          <p className="recommendation-card-address">{address}</p>
        ) : null}
        <div className="recommendation-card-meta">
          {siteType ? <span>{siteType}</span> : null}
        </div>
        {reason ? <p className="recommendation-card-reason">{reason}</p> : null}
      </div>
      <div className="recommendation-card-actions">
        <button type="button" className="site-bar-link site-bar-link-primary" onClick={addFacility}>
          Request pre-assessment
        </button>
        <button
          type="button"
          className="site-bar-link site-bar-link-secondary"
          onClick={() => onAddToWishlist(recommendation)}
          disabled={!siteId || isWishlisted || isAddingWishlist}
        >
          {isWishlisted ? "Added to wishlist" : isAddingWishlist ? "Adding..." : "Add to wishlist"}
        </button>
      </div>
    </article>
  );
}

function RecommendationSection({ title, recommendations, fallbackCompanyName, wishlistedSiteIds, addingWishlistSiteId, onAddToWishlist }) {
  const visibleRecommendations = recommendations
    .filter(hasHydratedRecommendationDetails)
    .slice(0, 3);
  if (!visibleRecommendations.length) return null;
  const layoutClass =
    visibleRecommendations.length === 1 ? "recommendation-list-single" : "recommendation-list-grid";
  return (
    <section className="recommendation-section">
      <h2 className="workspace-card-title">{title}</h2>
      <div className={`recommendation-list ${layoutClass}`}>
        {visibleRecommendations.map((recommendation, index) => (
          <RecommendationCard
            key={recommendation.place_id || `${title}-${recommendationAddress(recommendation)}-${index}`}
            recommendation={recommendation}
            fallbackCompanyName={fallbackCompanyName}
            wishlistedSiteIds={wishlistedSiteIds}
            addingWishlistSiteId={addingWishlistSiteId}
            onAddToWishlist={onAddToWishlist}
          />
        ))}
      </div>
    </section>
  );
}

function RecommendationsPanel({ selectedSite, wishlist, addingWishlistSiteId, onAddToWishlist }) {
  const recommendations = normalizeRecommendations(selectedSite?.recommendations);
  const status = recommendations.status;
  const ready = status === "ready";
  const companySites = recommendations.company_sites;
  const nearbySites = recommendations.nearby_sites;
  const hasReadyResults = companySites.length > 0 || nearbySites.length > 0;
  const wishlistedSiteIds = new Set((wishlist || []).map((item) => item.site_id).filter(Boolean));

  if (!ready) {
    return (
      <div className="report-running-panel">
        <div className="thank-you-icon thank-you-icon-muted" aria-hidden="true">
          ...
        </div>
        <p className="workspace-eyebrow">Job running</p>
        <h2 className="workspace-page-title">Recommendations are still running</h2>
        <p className="workspace-page-copy">
          We’re still preparing recommendations for this facility. They'll appear here when the job is complete.
        </p>
      </div>
    );
  }

  if (!hasReadyResults) {
    return (
      <div className="report-running-panel">
        <div className="thank-you-icon thank-you-icon-muted" aria-hidden="true">
          !
        </div>
        <p className="workspace-eyebrow">Recommendations</p>
        <h2 className="workspace-page-title">No recommendations available</h2>
        <p className="workspace-page-copy">
          There are no additional facility recommendations available for this site yet.
        </p>
      </div>
    );
  }

  return (
    <div className="recommendations-panel">
      <RecommendationSection
        title="More facilities from this company"
        recommendations={companySites}
        fallbackCompanyName={selectedSite?.company_name || ""}
        wishlistedSiteIds={wishlistedSiteIds}
        addingWishlistSiteId={addingWishlistSiteId}
        onAddToWishlist={onAddToWishlist}
      />
      <RecommendationSection
        title="Nearby facilities"
        recommendations={nearbySites}
        fallbackCompanyName=""
        wishlistedSiteIds={wishlistedSiteIds}
        addingWishlistSiteId={addingWishlistSiteId}
        onAddToWishlist={onAddToWishlist}
      />
    </div>
  );
}

function ReportPage() {
  const location = useLocation();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const [session, setSession] = useRequireSession();
  const [error, setError] = useState("");
  const [workspace, setWorkspace] = useState(() => session || loadSession());
  const [loadingWorkspace, setLoadingWorkspace] = useState(false);
  const [activeReportTab, setActiveReportTab] = useState(() =>
    location.state?.activeTab === "notes" || location.state?.activeTab === "recommendations"
      ? location.state.activeTab
      : "preAssessment",
  );
  const [notesDraft, setNotesDraft] = useState("");
  const [savingNotes, setSavingNotes] = useState(false);
  const [notesMessage, setNotesMessage] = useState("");
  const [notesIsError, setNotesIsError] = useState(false);
  const [ratingDraft, setRatingDraft] = useState(() => normalizeReportRatingMetadata({}));
  const [savingRatingField, setSavingRatingField] = useState("");
  const [savingRatingFeedback, setSavingRatingFeedback] = useState(false);
  const [ratingMessage, setRatingMessage] = useState("");
  const [ratingIsError, setRatingIsError] = useState(false);
  const [addingWishlistSiteId, setAddingWishlistSiteId] = useState("");
  const [showShareDialog, setShowShareDialog] = useState(false);
  const queryRouteState = {
    accountId: searchParams.get("account_id") || "",
    siteId: searchParams.get("site_id") || "",
    customerSiteId: searchParams.get("customer_site_id") || "",
  };
  const storedReportContext = loadReportContext();
  const routeState =
    location.state ||
    (queryRouteState.accountId || queryRouteState.siteId || queryRouteState.customerSiteId
      ? queryRouteState
      : null) ||
    storedReportContext ||
    {};
  const accountId = routeState.accountId || session?.activeAccountId || "";
  const siteId = routeState.siteId || "";
  const customerSiteId = routeState.customerSiteId || "";

  useEffect(() => {
    if (routeState.accountId || routeState.siteId || routeState.customerSiteId) {
      saveReportContext(routeState);
    }
    if (location.search) {
      navigate("/workspace/report", { replace: true, state: routeState });
    }
  }, [location.search, navigate, routeState.accountId, routeState.siteId, routeState.customerSiteId]);

  useEffect(() => {
    if (
      routeState.activeTab === "notes" ||
      routeState.activeTab === "preAssessment" ||
      routeState.activeTab === "recommendations"
    ) {
      setActiveReportTab(routeState.activeTab);
    }
  }, [routeState.activeTab]);

  useEffect(() => {
    if (!session?.email) return;
    setLoadingWorkspace(true);
    fetchJson("/api/workspace/state", {
      method: "POST",
      body: JSON.stringify({
        email: session.email,
        active_account_id: accountId,
      }),
    })
      .then((payload) => {
        const nextState = buildSessionFromPayload(session, payload);
        saveSession(nextState);
        setSession(nextState);
        setWorkspace(nextState);
      })
      .catch((nextError) => setError(nextError.message || "Could not load the saved site report."))
      .finally(() => setLoadingWorkspace(false));
  }, [session?.email, accountId]);

  const selectedSite =
    findWorkspaceSite(workspace?.sites, { siteId, customerSiteId }) || null;
  const reportMetadata = selectedSite?.report_metadata || {};
  const reportMarkedReady = Boolean(selectedSite?.is_report_ready);
  const reportHasMetadata = hasReportMetadata(reportMetadata);
  const requestedAt = selectedSite?.customer_site_metadata?.last_pre_assessment_requested_at || "";
  const generatedAt = selectedSite?.customer_site_metadata?.last_pre_assessment_generated_at || "";
  const reportGeneratedDate = formatReportDate(generatedAt);

  useEffect(() => {
    setNotesDraft(selectedSite?.notes || "");
    setNotesMessage("");
    setNotesIsError(false);
    setRatingDraft(normalizeReportRatingMetadata(selectedSite?.rating_metadata));
    setSavingRatingField("");
    setSavingRatingFeedback(false);
    setRatingMessage("");
    setRatingIsError(false);
  }, [selectedSite?.customer_site_id, selectedSite?.site_id]);

  function updateSiteInState(updates) {
    const updateSites = (state) => {
      if (!state || !Array.isArray(state.sites) || !selectedSite) return state;
      return {
        ...state,
        sites: state.sites.map((site) =>
          site.customer_site_id === selectedSite.customer_site_id ? { ...site, ...updates } : site,
        ),
      };
    };
    setWorkspace((current) => updateSites(current));
    const nextSession = updateSites(session);
    setSession(nextSession);
    saveSession(nextSession);
  }

  function updateWishlistInState(nextItem) {
    const updateWishlist = (state) => {
      if (!state || !nextItem?.site_id) return state;
      const currentWishlist = Array.isArray(state.wishlist) ? state.wishlist : [];
      const exists = currentWishlist.some((item) => item.site_id === nextItem.site_id);
      const nextWishlist = exists
        ? currentWishlist.map((item) => (item.site_id === nextItem.site_id ? { ...item, ...nextItem } : item))
        : [nextItem, ...currentWishlist];
      return { ...state, wishlist: nextWishlist };
    };
    setWorkspace((current) => updateWishlist(current));
    const nextSession = updateWishlist(session);
    setSession(nextSession);
    saveSession(nextSession);
  }

  async function addRecommendationToWishlist(recommendation) {
    if (!session?.email) return;
    const recommendationSiteId = recommendationText(recommendation.site_id);
    const recommendationAccountId = recommendationText(recommendation.account_id);
    if (!recommendationSiteId || !recommendationAccountId) {
      setError("This recommendation is missing site details and cannot be added to wishlist.");
      return;
    }
    setError("");
    setAddingWishlistSiteId(recommendationSiteId);
    try {
      const payload = await fetchJson("/api/customer-context/wishlist", {
        method: "POST",
        body: JSON.stringify({
          email: session.email,
          account_id: recommendationAccountId,
          site_id: recommendationSiteId,
          metadata: {
            source: "report_recommendations",
            source_site_id: selectedSite?.site_id || siteId,
            title: recommendationTitle(recommendation),
            address: recommendationAddress(recommendation),
            google_maps_uri: recommendationText(recommendation.google_maps_uri),
            source_url: recommendationText(recommendation.source_url),
          },
        }),
      });
      updateWishlistInState(payload.wishlist_item);
    } catch (nextError) {
      setError(nextError.message || "Could not add this site to wishlist.");
    } finally {
      setAddingWishlistSiteId("");
    }
  }

  async function saveNotes(event) {
    event.preventDefault();
    if (!selectedSite || !session?.email) return;
    setSavingNotes(true);
    setNotesMessage("");
    setNotesIsError(false);
    try {
      const payload = await fetchJson("/api/customer-sites/notes", {
        method: "POST",
        body: JSON.stringify({
          email: session.email,
          account_id: selectedSite.account_id || accountId,
          site_id: selectedSite.site_id || siteId,
          notes: notesDraft,
        }),
      });
      const savedNotes = payload.notes || "";
      setNotesDraft(savedNotes);
      updateSiteInState({ notes: savedNotes });
      setNotesMessage("Notes saved.");
    } catch (nextError) {
      setNotesIsError(true);
      setNotesMessage(nextError.message || "Could not save notes.");
    } finally {
      setSavingNotes(false);
    }
  }

  async function saveReportRating(nextRating, { fieldKey = "", successMessage = "Feedback saved." } = {}) {
    if (!selectedSite || !session?.email) return;
    if (fieldKey) {
      setSavingRatingField(fieldKey);
    } else {
      setSavingRatingFeedback(true);
    }
    setRatingMessage("");
    setRatingIsError(false);
    try {
      const payload = await fetchJson("/api/customer-sites/rating", {
        method: "POST",
        body: JSON.stringify({
          email: session.email,
          account_id: selectedSite.account_id || accountId,
          site_id: selectedSite.site_id || siteId,
          coverage: nextRating.coverage,
          accuracy: nextRating.accuracy,
          value: nextRating.value,
          additional_feedback: nextRating.additional_feedback,
        }),
      });
      const savedRating = normalizeReportRatingMetadata(payload.rating_metadata);
      setRatingDraft(savedRating);
      updateSiteInState({ rating_metadata: savedRating });
      setRatingMessage(successMessage);
    } catch (nextError) {
      setRatingIsError(true);
      setRatingMessage(nextError.message || "Could not save feedback.");
    } finally {
      setSavingRatingField("");
      setSavingRatingFeedback(false);
    }
  }

  function selectReportRating(fieldKey, value) {
    const nextRating = normalizeReportRatingMetadata({ ...ratingDraft, [fieldKey]: value });
    setRatingDraft(nextRating);
    saveReportRating(nextRating, { fieldKey, successMessage: "Rating saved." });
  }

  function saveAdditionalFeedback(event) {
    event.preventDefault();
    saveReportRating(ratingDraft, { successMessage: "Feedback saved." });
  }

  if (!session?.email) return null;

  return (
    <main className="workspace-page-shell signup-body workspace-body">
      <section className="workspace-page workspace-form-page report-page">
        <p className={`form-error ${error ? "" : "hidden"}`}>{error}</p>

        {loadingWorkspace && !workspace ? (
          <section className="workspace-card workspace-card-modern workspace-card-wide thank-you-state">
            <div className="workspace-loading-state">
              <p>Loading report state...</p>
            </div>
          </section>
        ) : null}

        {!loadingWorkspace && !selectedSite ? (
          <section className="workspace-card workspace-card-modern workspace-card-wide thank-you-state">
            <div className="thank-you-icon thank-you-icon-muted" aria-hidden="true">
              !
            </div>
            <h1 className="workspace-page-title">Site not found</h1>
            <p className="workspace-page-copy">
              Open this screen from a saved site in the workspace so we can load the correct report state.
            </p>
            <div className="auth-primary-action">
              <Link to="/workspace" className="btn-primary">
                Back to workspace
              </Link>
            </div>
          </section>
        ) : null}

        {selectedSite ? (
          <section className="workspace-card workspace-card-modern workspace-card-wide report-view-card">
            <div className="tab-row report-tab-row" role="tablist" aria-label="Report sections">
              <button
                type="button"
                className={`tab-btn ${activeReportTab === "preAssessment" ? "tab-btn-active" : ""}`}
                onClick={() => setActiveReportTab("preAssessment")}
                role="tab"
                aria-selected={activeReportTab === "preAssessment"}
              >
                Pre-assessment
              </button>
              <button
                type="button"
                className={`tab-btn ${activeReportTab === "notes" ? "tab-btn-active" : ""}`}
                onClick={() => setActiveReportTab("notes")}
                role="tab"
                aria-selected={activeReportTab === "notes"}
              >
                Notes
              </button>
              <button
                type="button"
                className={`tab-btn ${activeReportTab === "recommendations" ? "tab-btn-active" : ""}`}
                onClick={() => setActiveReportTab("recommendations")}
                role="tab"
                aria-selected={activeReportTab === "recommendations"}
              >
                Recommendations
              </button>
              {reportMarkedReady ? (
                <button
                  type="button"
                  className="btn-secondary report-share-button"
                  onClick={() => setShowShareDialog(true)}
                  aria-label="Share this report"
                >
                  <svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                    <circle cx="18" cy="5" r="3" />
                    <circle cx="6" cy="12" r="3" />
                    <circle cx="18" cy="19" r="3" />
                    <line x1="8.59" y1="13.51" x2="15.42" y2="17.49" />
                    <line x1="15.41" y1="6.51" x2="8.59" y2="10.49" />
                  </svg>
                  Share
                </button>
              ) : null}
            </div>

            {activeReportTab === "preAssessment" ? (
              <div className="tab-panel report-tab-panel" role="tabpanel">
                {reportMarkedReady ? (
                  reportHasMetadata ? (
                    <StructuredPreAssessmentReport
                      reportData={reportMetadata}
                      reportGeneratedDate={reportGeneratedDate}
                    />
                  ) : (
                    <StructuredReportUnavailable />
                  )
                ) : (
                  <div className="report-running-panel">
                    <div className="thank-you-icon thank-you-icon-muted" aria-hidden="true">
                      ...
                    </div>
                    <p className="workspace-eyebrow">Job running</p>
                    <h2 className="workspace-page-title">Your report is still running</h2>
                    <p className="workspace-page-copy">
                      We’re still generating the pre-assessment report for this site. You’ll receive an email when it
                      is complete.
                    </p>
                    <div className="pre-assessment-summary-grid">
                      <div className="workspace-summary-chip">
                        <span className="workspace-summary-label">Requested</span>
                        <span className="workspace-summary-value">
                          {requestedAt ? formatDateTime(requestedAt) : "Waiting for the first request"}
                        </span>
                      </div>
                      <div className="workspace-summary-chip">
                        <span className="workspace-summary-label">Status</span>
                        <span className="workspace-summary-value">In progress</span>
                      </div>
                    </div>
                  </div>
                )}
                {reportMarkedReady ? (
                  <ReportRatingPanel
                    rating={ratingDraft}
                    savingField={savingRatingField}
                    savingFeedback={savingRatingFeedback}
                    message={ratingMessage}
                    isError={ratingIsError}
                    onSelectRating={selectReportRating}
                    onFeedbackChange={(value) => {
                      setRatingDraft((current) => ({ ...current, additional_feedback: value }));
                      setRatingMessage("");
                      setRatingIsError(false);
                    }}
                    onSaveFeedback={saveAdditionalFeedback}
                  />
                ) : null}
              </div>
            ) : null}

            {activeReportTab === "notes" ? (
              <form className="tab-panel report-tab-panel report-notes-form" onSubmit={saveNotes} role="tabpanel">
                <label className="workspace-field report-notes-field">
                  <span>Notes</span>
                  <textarea
                    value={notesDraft}
                    onChange={(event) => {
                      setNotesDraft(event.target.value);
                      setNotesMessage("");
                      setNotesIsError(false);
                    }}
                    rows={10}
                    placeholder="Add notes for this facility."
                  />
                </label>
                <div className="report-notes-actions">
                  <button type="submit" className="btn-primary" disabled={savingNotes}>
                    {savingNotes ? "Saving..." : "Save notes"}
                  </button>
                  <p className={`workspace-feedback ${notesMessage ? "" : "hidden"} ${notesIsError ? "workspace-feedback-error" : ""}`}>
                    {notesMessage}
                  </p>
                </div>
              </form>
            ) : null}

            {activeReportTab === "recommendations" ? (
              <div className="tab-panel report-tab-panel" role="tabpanel">
                <RecommendationsPanel
                  selectedSite={selectedSite}
                  wishlist={workspace?.wishlist || []}
                  addingWishlistSiteId={addingWishlistSiteId}
                  onAddToWishlist={addRecommendationToWishlist}
                />
              </div>
            ) : null}
          </section>
        ) : null}
        {selectedSite ? (
          <nav className="report-mobile-actions" aria-label="Report actions">
            <button
              type="button"
              className={`report-mobile-action ${activeReportTab === "preAssessment" ? "active" : ""}`}
              onClick={() => setActiveReportTab("preAssessment")}
            >
              <svg aria-hidden="true" viewBox="0 0 24 24" fill="none">
                <path d="M7 3.5h7l3 3V20.5H7z" />
                <path d="M14 3.5v4h4" />
                <path d="M9.5 11h5" />
                <path d="M9.5 14h5" />
                <path d="M9.5 17h3" />
              </svg>
              <span>Pre-assessment</span>
            </button>
            <button
              type="button"
              className={`report-mobile-action ${activeReportTab === "notes" ? "active" : ""}`}
              onClick={() => setActiveReportTab("notes")}
            >
              <svg aria-hidden="true" viewBox="0 0 24 24" fill="none">
                <path d="M6.5 4.5h11v15h-11z" />
                <path d="M9 8h6" />
                <path d="M9 11.5h6" />
                <path d="M9 15h3.5" />
              </svg>
              <span>Notes</span>
            </button>
            <button
              type="button"
              className={`report-mobile-action ${activeReportTab === "recommendations" ? "active" : ""}`}
              onClick={() => setActiveReportTab("recommendations")}
            >
              <svg aria-hidden="true" viewBox="0 0 24 24" fill="none">
                <path d="M4.5 6.5h15" />
                <path d="M4.5 12h15" />
                <path d="M4.5 17.5h15" />
                <path d="M8 4v15" />
                <path d="M16 4v15" />
              </svg>
              <span>Recommendations</span>
            </button>
          </nav>
        ) : null}
        {showShareDialog && selectedSite && reportMarkedReady ? (
          <ShareReportDialog
            report={{
              customer_site_id: selectedSite.customer_site_id,
              company_name: selectedSite.company_name,
            }}
            senderEmail={session?.email || ""}
            onClose={() => setShowShareDialog(false)}
          />
        ) : null}
      </section>
    </main>
  );
}

function App() {
  const location = useLocation();
  useEffect(() => {
    document.title =
      location.pathname === "/"
        ? "AutomatiSOR — Warehouse Automation Advisor"
        : "AutomatiSOR";
    if (location.pathname === "/auth" || location.pathname === "/new-user") {
      document.body.className = "signup-body";
    } else if (location.pathname.startsWith("/workspace") || location.pathname.startsWith("/sample-reports")) {
      document.body.className = "signup-body workspace-body";
    } else {
      document.body.className = "";
    }
    return () => {
      document.body.className = "";
    };
  }, [location.pathname]);

  return (
    <Routes>
      <Route path="/" element={<Navigate to="/auth" replace />} />
      <Route path="/auth" element={<NewUserPage />} />
      <Route path="/new-user" element={<Navigate to="/auth" replace />} />
      <Route element={<WorkspaceLayout />}>
        <Route path="/workspace" element={<WorkspacePage />} />
        <Route path="/workspace/wishlist" element={<WishlistPage />} />
        <Route path="/workspace/sites/new" element={<NewSitePage />} />
        <Route path="/workspace/pre-assessment" element={<PreAssessmentPage />} />
        <Route path="/workspace/report" element={<ReportPage />} />
        <Route path="/workspace/credits" element={<CreditsPage />} />
        <Route path="/workspace/billing" element={<BillingPage />} />
      </Route>
      <Route path="/sample-reports/br-williams" element={<SampleReportPage />} />
      <Route path="*" element={<Navigate to="/auth" replace />} />
    </Routes>
  );
}

export default App;
