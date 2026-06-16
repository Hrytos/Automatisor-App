# Report Sharing Implementation

**Version:** 1.0  
**Date:** June 12, 2026  

---

## Overview

The Report Sharing feature allows users to share facility pre-assessment reports with colleagues via email. Recipients receive secure, tokenized links that grant them access to the shared report without requiring a payment card for their first site.

### Key Features

- **Secure token-based sharing** with HMAC signatures
- **Work email validation** (blocks personal email domains)
- **Multiple sharers support** (same report can be shared by different users)
- **Duplicate detection** (prevents re-sharing to the same recipient)
- **Rate-limited email sending** (Resend API free tier compatible)
- **Granular access control** (shared sites tracked separately in workspace)

---

## Architecture

### System Flow

```
┌─────────────┐     Share      ┌──────────────┐     Email      ┌─────────────┐
│   Sender    │───────────────>│   Backend    │───────────────>│  Recipient  │
│  (Report)   │   POST /share  │  (API+Email) │   Resend API   │   (Email)   │
└─────────────┘                └──────────────┘                └─────────────┘
                                       │
                                       v
                            ┌──────────────────┐
                            │  Database        │
                            │  - Token stored  │
                            │  - shared_by FK  │
                            └──────────────────┘
                                       │
                                       v
┌─────────────┐   Click Link   ┌──────────────┐    Auto-      ┌─────────────┐
│  Recipient  │───────────────>│    Auth      │─────────────>│   Shared    │
│   (Email)   │   /auth?share  │   + OTP      │   assign      │   Report    │
└─────────────┘                └──────────────┘               └─────────────┘
```

### Database Schema

**Table:** `automatisor_customer_sites`

```sql
CREATE TABLE automatisor_customer_sites (
  customer_site_id uuid PRIMARY KEY,
  customer_id uuid NOT NULL,
  site_id uuid NOT NULL,
  account_id uuid NOT NULL,
  assigned_via text NOT NULL CHECK (assigned_via IN ('user_added_site', 'shared_site')),
  shared_by uuid NULL,  -- FK to automatisor_customer (sharer)
  -- ... other fields
  CONSTRAINT automatisor_customer_sites_shared_by_fkey 
    FOREIGN KEY (shared_by) REFERENCES automatisor_customer(customer_id) ON DELETE SET NULL
);

-- Composite unique index for shared sites
CREATE UNIQUE INDEX idx_automatisor_customer_sites_shared_site_v2
  ON automatisor_customer_sites (customer_id, site_id, shared_by)
  WHERE assigned_via = 'shared_site';

-- Index for owned sites
CREATE UNIQUE INDEX idx_automatisor_customer_sites_owned_site
  ON automatisor_customer_sites (customer_id, site_id)
  WHERE assigned_via = 'user_added_site';
```

**Key Design Decisions:**

1. **Composite Unique Index** `(customer_id, site_id, shared_by)`:
   - Allows multiple users to share the same site to the same recipient
   - Prevents duplicate shares from the same user
   - Separate rows for each sharer

2. **Nullable `shared_by`**:
   - `NULL` for owned sites (`user_added_site`)
   - `uuid` for shared sites (tracks who shared it)

3. **`ON DELETE SET NULL`**:
   - If sharer account is deleted, shared site remains accessible
   - Preserves recipient's access to shared reports

---

## Backend Implementation

### Environment Variables

```bash
# Required
SHARE_TOKEN_SECRET=<32+ character random string>
APP_BASE_URL=https://app.automatisor.com
RESEND_API_KEY=<your_resend_api_key>
RESEND_FROM_EMAIL=notifications@automatisor.com
```

### API Endpoints

#### 1. `POST /api/reports/share`

Share a report with one or more recipients.

**Request:**
```json
{
  "email": "sender@company.com",
  "site_id": "uuid-of-site",
  "recipient_emails": ["colleague1@company.com", "colleague2@company.com"]
}
```

**Response:**
```json
{
  "sent": 2,
  "failed": 0,
  "already_shared": 0,
  "results": [
    {"email": "colleague1@company.com", "status": "sent"},
    {"email": "colleague2@company.com", "status": "sent"}
  ]
}
```

**Status Values:**
- `sent` - Email delivered successfully
- `already_shared` - User already shared with this recipient
- `failed` - Email delivery failed (includes error reason)

**Validations:**
- Report must be marked as ready (`is_report_ready = true`)
- Recipient emails must be work emails (not gmail, yahoo, outlook, etc.)
- Cannot share with self
- Rate limited to 3 concurrent email sends (Resend free tier)

#### 2. `POST /api/share/resolve`

Validate a share token and return site details.

**Request:**
```json
{
  "share_token": "base64url_encoded_token"
}
```

**Response:**
```json
{
  "valid": true,
  "recipient_email": "recipient@company.com",
  "site_name": "Company HQ",
  "site_id": "uuid",
  "sharer_email": "sender@company.com"
}
```

#### 3. `POST /api/auth/verify-otp` (Enhanced)

Existing OTP verification endpoint now handles share tokens.

**Request:**
```json
{
  "email": "recipient@company.com",
  "otp": "123456",
  "share_token": "base64url_encoded_token"  // Optional
}
```

**Response (with share):**
```json
{
  "valid": true,
  "session": { ... },
  "share_destination": "/workspace/report?site_id=uuid"  // Direct link
}
```

### Core Functions

#### Token Generation

```python
def encode_share_token(recipient_email: str, site_id: str, shared_by_customer_id: str) -> str:
    """
    Creates HMAC-signed share token.
    
    Token structure:
    {
      "recipient_email": "user@company.com",
      "site_id": "uuid",
      "shared_by": "uuid"
    }
    
    Returns: base64url(payload + signature)
    """
```

#### Email Validation

```python
def validate_share_recipients(
    sender_email: str,
    recipient_emails: list[str],
    site_id: str,
    customer_id: str,
    db
) -> dict:
    """
    Validates recipient emails and checks for duplicates.
    
    Returns:
    {
      "valid": ["user1@company.com"],
      "invalid": [],
      "already_shared": [],
      "errors": {}
    }
    """
```

#### Site Assignment

```python
async def ensure_shared_site_assignment(
    db,
    recipient_customer_id: str,
    site_id: str,
    shared_by_customer_id: str
):
    """
    Creates shared site assignment with idempotency check.
    
    Checks (customer_id, site_id, shared_by) tuple.
    Copies report metadata from source if available.
    """
```

---

## Frontend Implementation

### Components

#### 1. ShareReportDialog

**File:** `frontend/src/ShareReportDialog.jsx`

Standalone React component for sharing reports.

**Props:**
- `isOpen` - Boolean to control visibility
- `onClose` - Callback when modal closes
- `siteId` - UUID of site to share
- `siteName` - Name of site (for display)
- `senderEmail` - Email of current user

**Features:**
- Multi-line textarea for recipient emails (comma/line/semicolon separated)
- Client-side work email validation using `free-email-domains-list` npm package
- Duplicate detection within input
- Self-sharing prevention
- Per-recipient status display with color coding
- Loading states and error handling

**Usage:**
```jsx
<ShareReportDialog
  isOpen={showShareDialog}
  onClose={() => setShowShareDialog(false)}
  siteId={selectedSite.site_id}
  siteName={selectedSite.company_name}
  senderEmail={session.email}
/>
```

### UI Integration Points

#### 1. Report Page - Share Button

**Location:** Report tab row (Pre-assessment | Notes | Recommendations | **Share**)

**Visibility:** Only shown when `is_report_ready === true`

**Behavior:** Opens ShareReportDialog modal

#### 2. Workspace Page - Shared Facilities Section

**Location:** Workspace facilities page with tabs

**Tabs:**
- **My facilities** - Sites with `assigned_via !== 'shared_site'`
- **Shared facilities** - Sites with `assigned_via === 'shared_site'`

**Conditional Rendering:** Shared facilities tab only visible if user has shared sites

#### 3. Site Row - Share Button

**Location:** Individual facility rows in workspace

**Behavior:** Opens ShareReportDialog for that specific site

#### 4. Auth Page - Share Link Handling

**URL:** `/auth?share=TOKEN`

**Flow:**
1. Extracts share token from URL
2. Calls `POST /api/share/resolve` to validate
3. Pre-fills email field (read-only for share recipients)
4. Shows contextual message with company name
5. After OTP verification, navigates to `share_destination`

### Styling

**Key CSS Classes:**
- `.report-share-button` - Share button styling with icon
- `.share-report-modal` - Modal container and overlay
- `.share-recipient-results` - Results container
- `.share-result-sent` - Success status (green)
- `.share-result-warning` - Already shared status (orange)
- `.share-result-failed` - Failed status (red)

---

## Security

### Token Security

**HMAC Signing:**
- Tokens signed with `SHARE_TOKEN_SECRET`
- Signature prevents tampering
- Token payload is visible but not modifiable

**Token Structure:**
```
base64url({
  "recipient_email": "user@company.com",
  "site_id": "uuid",
  "shared_by": "uuid"
} + hmac_signature)
```

### Email Validation

**Work Email Enforcement:**
- Uses `free-email-domains-list` package (3000+ personal domains)
- Blocks: gmail.com, yahoo.com, outlook.com, hotmail.com, etc.
- Enforced on both client and server

**Validation Rules:**
- Must be valid email format
- Cannot be sender's own email
- Must not be a personal email domain
- Normalized to lowercase for consistency

### Rate Limiting

**Resend API Protection:**
- `asyncio.Semaphore(3)` limits concurrent sends
- Safe for Resend free tier (10 req/sec limit)
- Graceful error handling for rate limit errors

---

## Deployment

**Steps:**
1. Add `shared_by` column (nullable UUID)
2. Drop old unique index `idx_automatisor_customer_sites_shared_site`
3. Create new composite unique index for shared sites
4. Create unique index for owned sites
5. Add FK constraint with `ON DELETE SET NULL`

**Run migration:**
```bash
# Connect to Supabase SQL Editor and run:
backend/schema/add_shared_by_column_and_fix_constraints.sql
```

**Verification queries:**
```sql
-- Check column exists
SELECT column_name, data_type, is_nullable 
FROM information_schema.columns 
WHERE table_name = 'automatisor_customer_sites' AND column_name = 'shared_by';

-- Check indexes
SELECT indexname, indexdef 
FROM pg_indexes 
WHERE tablename = 'automatisor_customer_sites';
```

### Environment Setup

**Production:**
```bash
SHARE_TOKEN_SECRET=<generate-with: openssl rand -base64 32>
APP_BASE_URL=https://app.automatisor.com
RESEND_FROM_EMAIL=notifications@automatisor.com
```

**Staging:**
```bash
SHARE_TOKEN_SECRET=<different-secret-for-staging>
APP_BASE_URL=https://staging.automatisor.com
RESEND_FROM_EMAIL=notifications@automatisor.com
```

### Testing Checklist

**Backend:**
- [ ] Run tests: `pytest backend/test_report_sharing.py`
- [ ] Test `POST /api/reports/share` with valid emails
- [ ] Test duplicate share detection
- [ ] Test personal email rejection
- [ ] Test rate limiting with 10+ recipients
- [ ] Test token generation and validation

**Frontend:**
- [ ] Test share button visibility on ready reports
- [ ] Test ShareReportDialog opens and closes
- [ ] Test email input validation (work emails only)
- [ ] Test duplicate detection in textarea
- [ ] Test share link in email (`/auth?share=TOKEN`)
- [ ] Test OTP flow with share token
- [ ] Test shared facilities section appears
- [ ] Test shared facilities section hidden when empty

**End-to-End:**
- [ ] Share report from User A to User B
- [ ] User B receives email with share link
- [ ] User B clicks link and completes OTP
- [ ] User B sees report in "Shared facilities"
- [ ] User A shares same report to User C (should work)
- [ ] User A tries to re-share to User B (should show warning)

---

## Known Limitations & Future Enhancements

### Current Limitations

1. **No sharer visibility** - Recipients cannot see who shared the report with them
2. **No revoke functionality** - Once shared, cannot be un-shared
3. **No share notifications** - Sharer not notified when recipient views report
4. **No share analytics** - No tracking of conversion rates

### Future Enhancements

**Priority: High**
- Display sharer name/email in shared site rows
- Add "Revoke share" functionality
- Share activity log (who shared what, when)

**Priority: Medium**
- Email notifications when recipient views report
- Share analytics dashboard
- Batch share limits (UI warning for 10+ recipients)

**Priority: Low**
- Share expiration dates
- Custom share messages
- Share permissions (view-only vs. full access)

---

## Troubleshooting

### Common Issues

**Issue:** 402 Payment Required when adding first site after receiving shared report

**Solution:** Ensure backend query excludes shared sites:
```python
"assigned_via": "neq.shared_site"  # In payment gate check
```

**Issue:** Recipient doesn't receive email

**Checklist:**
- Verify Resend API key is valid
- Check recipient email is work email (not personal)
- Check Resend dashboard for delivery status
- Verify `RESEND_FROM_EMAIL` is authorized

**Issue:** Share link doesn't work

**Checklist:**
- Verify `SHARE_TOKEN_SECRET` matches between token creation and validation
- Check token hasn't expired (tokens are permanent unless secret changes)
- Verify site still exists in database
- Check browser console for API errors

**Issue:** Shared facilities not showing in workspace

**Checklist:**
- Verify `assigned_via = 'shared_site'` in database
- Check frontend filters: `site.assigned_via === "shared_site"`
- Ensure backend includes `assigned_via` in site query response
- Clear browser cache and hard reload

---

## Testing

### Unit Tests

**File:** `backend/test_report_sharing.py`

**Coverage:**
- Token encoding/decoding round-trip
- Token tampering detection
- Backward compatibility (old tokens without `shared_by`)
- Recipient email validation (all error types)
- Secret not exposed in frontend config

**Run tests:**
```bash
pytest backend/test_report_sharing.py -v
```

### Integration Testing

**Manual Test Script:**

1. **Setup:**
   - User A: sender@company.com (has ready report)
   - User B: recipient1@company.com (new user)
   - User C: recipient2@company.com (existing user)

2. **Test Share:**
   ```
   POST /api/reports/share
   {
     "email": "sender@company.com",
     "site_id": "uuid",
     "recipient_emails": ["recipient1@company.com", "recipient2@company.com"]
   }
   ```
   Expected: 200, sent=2

3. **Test Duplicate:**
   ```
   POST /api/reports/share (same payload)
   ```
   Expected: 200, already_shared=2

4. **Test Personal Email:**
   ```
   "recipient_emails": ["someone@gmail.com"]
   ```
   Expected: 200, failed=1, error="work email required"

5. **Test Recipient Flow:**
   - Check inbox for share email
   - Click link → lands on `/auth?share=TOKEN`
   - Email pre-filled and read-only
   - Complete OTP flow
   - Verify redirect to report
   - Check "Shared facilities" in workspace

---

## Performance

### Email Sending

**Concurrency:** 3 parallel sends (Semaphore(3))

**Throughput:**
- 10 emails ≈ 3-5 seconds
- 100 emails ≈ 30-50 seconds

**Scalability:**
- Resend free tier: 100 emails/day, 3,000 emails/month
- Upgrade to paid tier for higher volume

### Database Queries

**Indexed queries:**
- Site lookup by `site_id`: O(1) with index
- Customer sites by `customer_id`: O(1) with index
- Duplicate check `(customer_id, site_id, shared_by)`: O(1) with composite index

**Query optimization:**
- Use `limit=1` for existence checks
- Fetch only required columns with `select`
- Leverage partial indexes for `assigned_via` filtering

---

## Changelog

### v1.0 (June 12, 2026)
- Initial implementation
- HMAC-signed tokens
- Work email validation
- Multiple sharers support
- Duplicate detection
- Rate-limited email sending
- Workspace shared facilities section
- Share button on report page
- Auth page share link handling

---

## Support

For questions or issues, contact the development team or refer to:
- Backend code: `backend/main.py`
- Frontend component: `frontend/src/ShareReportDialog.jsx`
- Tests: `backend/test_report_sharing.py`
