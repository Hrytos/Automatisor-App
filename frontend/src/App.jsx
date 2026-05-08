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
  useLocation,
  Navigate,
  Route,
  Routes,
  useNavigate,
  useSearchParams,
} from "react-router-dom";

const SESSION_KEY = "automatisor_auth_workspace_v2";
const REPORT_CONTEXT_KEY = "automatisor_selected_report_v1";
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
    accounts: Array.isArray(payload.accounts) ? payload.accounts : session?.accounts || [],
    preAssessmentPriceCredits: Number(payload.pre_assessment_price_credits || session?.preAssessmentPriceCredits || 1),
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
  return "";
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

  return {
    full_address: String(formattedAddress || "").trim(),
    street: streetParts.join(" ").trim(),
    city:
      longText(byType.locality) ||
      longText(byType.postal_town) ||
      longText(byType.sublocality) ||
      longText(byType.administrative_area_level_2) ||
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

const GoogleAddressPicker = forwardRef(function GoogleAddressPicker(
  {
    inputId,
    googleButtonId,
    mapId,
    messageId,
    onResolvedChange,
    mapLabel,
  },
  ref,
) {
  const [selectedAddress, setSelectedAddress] = useState(DEFAULT_SITE_ADDRESS);
  const [message, setStatusMessage] = useState("");
  const [messageIsError, setMessageIsError] = useState(false);
  const [mapsLink, setMapsLink] = useState(buildGoogleMapsSearchLink(DEFAULT_SITE_ADDRESS));
  const [isGoogleReady, setIsGoogleReady] = useState(false);
  const [useLegacyAutocomplete, setUseLegacyAutocomplete] = useState(false);
  const resolvedRef = useRef(null);
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
          placeAutocomplete.setAttribute("placeholder", "Start typing a site address");
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

        reverseGeocodeLocation(DEFAULT_SITE_COORDS).catch(() => {
          setMapsLink(buildGoogleMapsSearchLink(DEFAULT_SITE_ADDRESS));
        });
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
        <span>Site address</span>
        {useLegacyAutocomplete ? (
          <div className="workspace-search-stack">
            <input
              id={inputId}
              ref={inputRef}
              type="text"
              className="place-autocomplete-legacy-input"
              placeholder="Start typing a site address"
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

function SiteRow({ site }) {
  const title = site.company_name || site.metadata?.site_name || "Saved site";
  const routeState = buildPreAssessmentRouteState(site);
  const reportReady = Boolean(site.is_report_ready);
  return (
    <article className="site-bar-item">
      <div className="site-bar-copy">
        <p className="site-bar-title">{title}</p>
        <p className="site-bar-address">{site.full_address || ""}</p>
      </div>
      <div className="site-bar-actions">
        <Link
          className="site-bar-link site-bar-link-primary"
          to={buildWorkspaceReportPath(routeState)}
          state={routeState}
          onClick={() => saveReportContext(routeState)}
        >
          View Report
        </Link>
        <p className="site-bar-meta">
          {reportReady
            ? "Report available"
            : site.customer_site_metadata?.last_pre_assessment_requested_at
              ? `Requested ${formatDateTime(site.customer_site_metadata.last_pre_assessment_requested_at)}`
              : "Job not started yet"}
        </p>
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
  };
}

function buildWorkspaceReportPath(siteOrPayload) {
  return "/workspace/report";
}

function buildPendingSiteFromInput(form, sitePayload) {
  return {
    account_id: "",
    site_id: "",
    company_name: form.org_name,
    org_name: form.org_name,
    org_domain: form.org_domain,
    full_address: sitePayload.full_address || sitePayload.fullAddress || "",
    street: sitePayload.street || "",
    city: sitePayload.city || "",
    state: sitePayload.state || "",
    zip: sitePayload.zip || "",
    country: sitePayload.country || "US",
    place_id: sitePayload.place_id || "",
    metadata: {
      site_name: form.org_name,
      site_type: "Pending pre-assessment site",
    },
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
            <span className="nav-logo-byline">by Hrytos</span>
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

function NewUserPage() {
  const navigate = useNavigate();
  const addressPickerRef = useRef(null);
  const [stage, setStage] = useState("email");
  const [email, setEmail] = useState("");
  const [emailError, setEmailError] = useState("");
  const [formError, setFormError] = useState("");
  const [otp, setOtp] = useState("");
  const [loading, setLoading] = useState("");
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
    designation: "",
    customer_company_name: "",
    customer_company_domain: "",
    site_company_name: "",
    site_company_domain: "",
    hasAddress: false,
  });
  const feedback = normalizeAuthFeedback(formError);

  useEffect(() => {
    const saved = loadSession();
    if (!saved) return;
    setSessionState((current) => ({ ...current, ...saved }));
    setEmail(saved.email || "");
    if (saved.nextStep === "otp") {
      setStage("otp");
    } else if (saved.nextStep === "onboarding" && saved.authVerified) {
      setStage("onboarding");
      setOnboarding((current) => ({
        ...current,
        customer_company_name: saved.companyName || "",
        customer_company_domain: saved.companyDomain || "",
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

  const onboardingReady = Boolean(
    onboarding.first_name &&
      onboarding.last_name &&
      onboarding.designation &&
      onboarding.customer_company_name &&
      onboarding.customer_company_domain &&
      onboarding.site_company_name &&
      onboarding.site_company_domain &&
      onboarding.hasAddress,
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
        body: JSON.stringify({ email: sessionState.email || email, otp }),
      });
      if (payload.next_step === "workspace") {
        persistState({
          email: payload.email || sessionState.email || email,
          userMode: payload.user_mode || sessionState.userMode,
          nextStep: "workspace",
          authVerified: true,
        });
        showWorkspace(payload);
      } else {
        persistState({
          email: payload.email || sessionState.email || email,
          userMode: payload.user_mode || sessionState.userMode,
          nextStep: "onboarding",
          authVerified: true,
        });
        setStage("onboarding");
        setOnboarding((current) => ({
          ...current,
          customer_company_name: sessionState.companyName || "",
          customer_company_domain: sessionState.companyDomain || "",
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

  async function completeOnboarding() {
    setFormError("");
    setLoading("onboarding");
    try {
      const sitePayload = await addressPickerRef.current.resolveCurrentAddress();
      const payload = await fetchJson("/api/onboarding/complete", {
        method: "POST",
        body: JSON.stringify({
          email: sessionState.email || email,
          first_name: onboarding.first_name,
          last_name: onboarding.last_name,
          designation: onboarding.designation,
          customer_company_name: onboarding.customer_company_name,
          customer_company_domain: onboarding.customer_company_domain,
          site_company_name: onboarding.site_company_name,
          site_company_domain: onboarding.site_company_domain,
          ...sitePayload,
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

  return (
    <div className="signup-body">
      <AppNav backToHome />
      <main className="signup-page signup-page-modern">
        <section className="auth-shell-modern">
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
                    : "Account does not exist. Sign up first to create your workspace."}
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
                  <p>Use a company email address. Personal inboxes are blocked.</p>
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

            {stage === "onboarding" ? (
              <section className="auth-stage-card auth-stage-card-wide">
                <div className="auth-stage-header">
                  <h3>Finish your onboarding</h3>
                  <p>Set up your user profile, company, and first site before requesting the first pre-assessment.</p>
                </div>

                <div className="modern-form-grid">
                  {[
                    ["First name", "first_name"],
                    ["Last name", "last_name"],
                    ["Designation", "designation"],
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
                  <label className="modern-field">
                    <span>Your company name</span>
                    <input
                      value={onboarding.customer_company_name}
                      onChange={(event) =>
                        setOnboarding((current) => ({
                          ...current,
                          customer_company_name: event.target.value,
                        }))
                      }
                      placeholder="Your company name"
                    />
                  </label>
                  <label className="modern-field modern-field-wide">
                    <span>Your company domain</span>
                    <input
                      value={onboarding.customer_company_domain}
                      onChange={(event) =>
                        setOnboarding((current) => ({
                          ...current,
                          customer_company_domain: event.target.value,
                        }))
                      }
                      placeholder="Your company domain"
                    />
                  </label>
                </div>

                <section className="site-card-modern">
                  <div className="site-card-head">
                    <div>
                      <p className="workspace-card-label">Add your first site</p>
                    </div>
                  </div>
                  <div className="modern-form-grid">
                    <label className="modern-field">
                      <span>Site company name</span>
                      <input
                        value={onboarding.site_company_name}
                        onChange={(event) =>
                          setOnboarding((current) => ({
                            ...current,
                            site_company_name: event.target.value,
                          }))
                        }
                        placeholder="Company name for this site"
                      />
                    </label>
                    <label className="modern-field">
                      <span>Site company domain</span>
                      <input
                        value={onboarding.site_company_domain}
                        onChange={(event) =>
                          setOnboarding((current) => ({
                            ...current,
                            site_company_domain: event.target.value,
                          }))
                        }
                        placeholder="Company domain for this site"
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
                    onResolvedChange={({ resolvedAddress, inputValue }) => {
                      setOnboarding((current) => ({
                        ...current,
                        hasAddress: Boolean((resolvedAddress || {}).full_address),
                      }));
                    }}
                  />
                </section>

                <div className="auth-primary-action">
                  <button
                    type="button"
                    className="btn-primary btn-submit-wide"
                    onClick={completeOnboarding}
                    disabled={!onboardingReady || loading === "onboarding"}
                  >
                    {loading === "onboarding" ? "Please wait..." : "Request pre-assessment"}
                  </button>
                </div>
              </section>
            ) : null}
          </section>
        </section>
      </main>
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

function WorkspacePage() {
  const navigate = useNavigate();
  const [session, setSession] = useRequireSession();
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
      .catch((nextError) => setError(nextError.message || "Could not load the workspace."))
      .finally(() => setLoadingWorkspace(false));
  }, [session?.email]);

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

  const sites = workspace?.sites || [];
  return (
    <main className="workspace-page-shell signup-body workspace-body">
      <section className="workspace-page">
        <header className="workspace-topbar">
          <div className="workspace-topbar-copy">
            <p className="workspace-eyebrow">Workspace</p>
            <h1 className="workspace-page-title">Saved sites</h1>
          </div>
          <div className="workspace-topbar-actions">
            <Link to="/workspace/sites/new" className="btn-primary">
              Add new site
            </Link>
            <button type="button" className="btn-secondary" onClick={logout}>
              Logout
            </button>
            <CreditsUsedChip
              creditsUsed={workspace?.creditsUsedTotal || 0}
            />
          </div>
        </header>

        <p className={`form-error ${error ? "" : "hidden"}`}>{error}</p>

        <section className="workspace-sites-panel">
          {loadingWorkspace && !sites.length ? (
            <div className="workspace-loading-state">
              <p>Loading saved sites...</p>
            </div>
          ) : sites.length ? (
            <div className="site-bar-list">
              {sites.map((site) => (
                <SiteRow key={site.site_id} site={site} />
              ))}
            </div>
          ) : (
            <div className="workspace-empty-state">
              <h3>No sites added yet</h3>
              <p>Add your first site to start organizing the workspace around real operating locations.</p>
              <Link to="/workspace/sites/new" className="btn-primary">
                Add first site
              </Link>
            </div>
          )}
        </section>
      </section>
    </main>
  );
}

function NewSitePage() {
  const navigate = useNavigate();
  const pickerRef = useRef(null);
  const [session, setSession] = useRequireSession();
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [loading, setLoading] = useState(false);
  const [form, setForm] = useState({ org_name: "", org_domain: "", hasAddress: false });

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

  const ready = Boolean(form.org_name && form.org_domain && form.hasAddress);

  async function continueToPreAssessment() {
    setError("");
    setMessage("");
    setLoading(true);
    try {
      const sitePayload = await pickerRef.current.resolveCurrentAddress();
      navigate("/workspace/pre-assessment", {
        state: {
          pendingSite: buildPendingSiteFromInput(form, sitePayload),
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
              <h1 className="workspace-page-title">Add a new site</h1>
              <p className="workspace-page-copy">
                Enter the account details and operating location. We will save the site only after
                you confirm the pre-assessment request.
              </p>
            </div>
            <Link to="/workspace" className="btn-primary">
              Back to workspace
            </Link>
          </div>
        </header>

        <p className={`form-error ${error ? "" : "hidden"}`}>{error}</p>

        <section className="workspace-card workspace-card-modern workspace-card-form workspace-card-wide">
          <div className="workspace-form-grid">
            <label className="workspace-field">
              <span>Company name</span>
              <input
                value={form.org_name}
                onChange={(event) => setForm((current) => ({ ...current, org_name: event.target.value }))}
                placeholder="Company name"
              />
            </label>
            <label className="workspace-field">
              <span>Company domain</span>
              <input
                value={form.org_domain}
                onChange={(event) => setForm((current) => ({ ...current, org_domain: event.target.value }))}
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
              onResolvedChange={({ resolvedAddress, inputValue }) =>
                setForm((current) => ({
                  ...current,
                  hasAddress: Boolean((resolvedAddress || {}).full_address),
                }))
              }
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
  const location = useLocation();
  const [searchParams] = useSearchParams();
  const [session, setSession] = useRequireSession();
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [tab, setTab] = useState("meaning");
  const [mode, setMode] = useState("flow");
  const [workspace, setWorkspace] = useState(() => session || loadSession());
  const [loadingWorkspace, setLoadingWorkspace] = useState(false);
  const routeState = location.state || {};
  const pendingSite = routeState.pendingSite || null;
  const accountId = routeState.accountId || searchParams.get("account_id") || session?.activeAccountId || "";
  const siteId = routeState.siteId || searchParams.get("site_id") || "";

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
    () => pendingSite || (workspace?.sites || []).find((site) => site.site_id === siteId) || null,
    [workspace, siteId, pendingSite],
  );
  const isPendingSite = Boolean(pendingSite);
  const isResolvingSelectedSite = loadingWorkspace && Boolean(siteId) && !selectedSite;
  const creditsUsed = workspace?.creditsUsedTotal ?? session?.creditsUsedTotal ?? 0;
  const preAssessmentPriceCredits =
    workspace?.preAssessmentPriceCredits ?? session?.preAssessmentPriceCredits ?? 1;

  async function requestPreAssessment() {
    if (!selectedSite) {
      setError("Select a site from the workspace before requesting a pre-assessment.");
      return;
    }
    setError("");
    setLoading(true);
    try {
      let requestAccountId = accountId || selectedSite.account_id || "";
      let requestSiteId = selectedSite.site_id || "";
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
          }),
        });
        nextWorkspace = buildSessionFromPayload(session, savedSitePayload);
        saveSession(nextWorkspace);
        setSession(nextWorkspace);
        setWorkspace(nextWorkspace);
        requestAccountId = savedSitePayload.account_id || nextWorkspace.activeAccountId || "";
        requestSiteId = savedSitePayload.site_id || "";
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
          confirmed: true,
        }),
      });
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
      setMode("success");
    } catch (nextError) {
      setError(nextError.message || "Could not request the pre-assessment.");
    } finally {
      setLoading(false);
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
                <Link to="/workspace" className="btn-primary">
                  Back to workspace
                </Link>
              </div>
            </header>

            <p className={`form-error ${error ? "" : "hidden"}`}>{error}</p>

            <section className="workspace-topbar-actions workspace-inline-stats">
              <CreditsUsedChip
                creditsUsed={creditsUsed}
              />
              <div className="wallet-chip wallet-chip-muted">
                <p className="wallet-chip-inline">
                  <span className="wallet-chip-inline-label">Price:</span>{" "}
                  <span className="wallet-chip-inline-value">
                    {preAssessmentPriceCredits} credit
                  </span>
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
              <div className="tab-row">
                {[
                  ["meaning", "What it means"],
                  ["benefits", "Benefits"],
                  ["pricing", "Pricing"],
                ].map(([key, label]) => (
                  <button
                    key={key}
                    className={`tab-btn ${tab === key ? "tab-btn-active" : ""}`}
                    type="button"
                    onClick={() => setTab(key)}
                  >
                    {label}
                  </button>
                ))}
              </div>

              {tab === "meaning" ? (
                <div className="tab-panel">
                  <h2 className="workspace-card-title">What a pre-assessment does</h2>
                  <p className="workspace-copy">
                    The pre-assessment creates the first operational view of your site before a
                    deeper workflow runs. It packages the location context, readiness signals, and
                    likely automation fit into one starting report for your team.
                  </p>
                </div>
              ) : null}

              {tab === "benefits" ? (
                <div className="tab-panel">
                  <h2 className="workspace-card-title">Why teams use it</h2>
                  <ul className="simple-list">
                    <li>It gives operators a clean site-level starting point before a broader automation discussion.</li>
                    <li>It highlights fit, constraints, and readiness signals in one request-driven workflow.</li>
                    <li>It creates a reusable report trail tied to the exact site in your workspace.</li>
                  </ul>
                </div>
              ) : null}

              {tab === "pricing" ? (
                <div className="tab-panel">
                  <h2 className="workspace-card-title">Current pricing</h2>
                  <p className="workspace-copy">
                    Each pre-assessment request adds{" "}
                    <strong>{preAssessmentPriceCredits} credit used</strong>. We
                    track usage here and bill monthly, so nothing is prepaid or blocked in-product.
                  </p>
                </div>
              ) : null}

              <div className="auth-primary-action">
                <button
                  type="button"
                  className="btn-primary btn-glow"
                  onClick={requestPreAssessment}
                  disabled={loading || !selectedSite}
                >
                  {loading ? "Please wait..." : "Confirm and start pre-assessment"}
                </button>
              </div>
            </section>
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
              <Link to="/workspace" className="btn-primary">
                Back to workspace
              </Link>
            </div>
          </section>
        ) : null}

      </section>
    </main>
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
  const [activeReportTab, setActiveReportTab] = useState("preAssessment");
  const [notesDraft, setNotesDraft] = useState("");
  const [savingNotes, setSavingNotes] = useState(false);
  const [notesMessage, setNotesMessage] = useState("");
  const [notesIsError, setNotesIsError] = useState(false);
  const queryRouteState = {
    accountId: searchParams.get("account_id") || "",
    siteId: searchParams.get("site_id") || "",
  };
  const storedReportContext = loadReportContext();
  const routeState =
    location.state ||
    (queryRouteState.accountId || queryRouteState.siteId ? queryRouteState : null) ||
    storedReportContext ||
    {};
  const accountId = routeState.accountId || session?.activeAccountId || "";
  const siteId = routeState.siteId || "";

  useEffect(() => {
    if (routeState.accountId || routeState.siteId) {
      saveReportContext(routeState);
    }
    if (location.search) {
      navigate("/workspace/report", { replace: true, state: routeState });
    }
  }, [location.search, routeState.accountId, routeState.siteId]);

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

  const selectedSite = (workspace?.sites || []).find((site) => site.site_id === siteId) || null;
  const reportMetadata = selectedSite?.report_metadata || {};
  const reportReady = hasReportMetadata(reportMetadata);
  const requestedAt = selectedSite?.customer_site_metadata?.last_pre_assessment_requested_at || "";
  const reportEntries = Object.entries(reportMetadata || {});

  useEffect(() => {
    setNotesDraft(selectedSite?.notes || "");
    setNotesMessage("");
    setNotesIsError(false);
  }, [selectedSite?.customer_site_id, selectedSite?.site_id]);

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
      const updateSites = (state) => {
        if (!state || !Array.isArray(state.sites)) return state;
        return {
          ...state,
          sites: state.sites.map((site) =>
            site.site_id === selectedSite.site_id ? { ...site, notes: savedNotes } : site,
          ),
        };
      };
      setNotesDraft(savedNotes);
      setWorkspace((current) => updateSites(current));
      const nextSession = updateSites(session);
      setSession(nextSession);
      saveSession(nextSession);
      setNotesMessage("Notes saved.");
    } catch (nextError) {
      setNotesIsError(true);
      setNotesMessage(nextError.message || "Could not save notes.");
    } finally {
      setSavingNotes(false);
    }
  }

  if (!session?.email) return null;

  return (
    <main className="workspace-page-shell signup-body workspace-body">
      <section className="workspace-page workspace-form-page report-page">
        <div className="report-page-actions">
          <Link to="/workspace" className="btn-primary">
            Back to workspace
          </Link>
        </div>

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
            </div>

            {activeReportTab === "preAssessment" ? (
              <div className="tab-panel report-tab-panel" role="tabpanel">
                {reportReady ? (
                  <>
                    <div className="workspace-sites-head">
                      <div>
                        <p className="workspace-card-label">Saved report</p>
                        <h2 className="workspace-card-title">
                          {selectedSite.company_name || "Pre-assessment report"}
                        </h2>
                        <p className="workspace-page-copy workspace-page-copy-tight">
                          This is the report data currently saved in <code>report_metadata</code> for this customer-site pair.
                        </p>
                      </div>
                    </div>
                    <div className="report-data-grid">
                      {reportEntries.map(([key, value]) => (
                        <div key={key} className="workspace-summary-chip report-data-chip">
                          <span className="workspace-summary-label">{key.replace(/_/g, " ")}</span>
                          <div className="workspace-summary-value report-data-value">{renderReportValue(value)}</div>
                        </div>
                      ))}
                    </div>
                  </>
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
                    placeholder="Add notes for this site."
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
          </section>
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
    } else if (location.pathname.startsWith("/workspace")) {
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
      <Route path="/workspace" element={<WorkspacePage />} />
      <Route path="/workspace/sites/new" element={<NewSitePage />} />
      <Route path="/workspace/pre-assessment" element={<PreAssessmentPage />} />
      <Route path="/workspace/report" element={<ReportPage />} />
      <Route path="*" element={<Navigate to="/auth" replace />} />
    </Routes>
  );
}

export default App;
