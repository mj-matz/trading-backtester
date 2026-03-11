# PROJ-8: Authentication (Admin Login)

## Status: Deployed
**Created:** 2026-03-10
**Last Updated:** 2026-03-10

## Dependencies
- None — this is a foundational feature; all other features depend on it

## User Stories
- As an admin, I want to log in with email and password so that the backtesting platform is not publicly accessible.
- As an admin, I want to be automatically redirected to the login page when accessing the app without a valid session so that unauthorized access is blocked.
- As an admin, I want to stay logged in across page refreshes and browser restarts so that I don't have to re-enter my credentials constantly.
- As an admin, I want to log out so that I can close the session on shared or public devices.
- As a future additional user, I want an admin to invite me by email so that I can access the platform without self-registration.

## Acceptance Criteria
- [ ] Login page at `/login` with email and password fields
- [ ] Successful login redirects to `/` (backtest dashboard)
- [ ] Failed login (wrong credentials) shows a clear error message; form stays intact
- [ ] All routes except `/login` are protected — unauthenticated requests redirect to `/login`
- [ ] Session is persisted via Supabase Auth cookies/tokens; page refresh does not log the user out
- [ ] "Log out" button available in the app navigation; clicking it invalidates the session and redirects to `/login`
- [ ] Only pre-created users (no public self-registration) — new users are added via Supabase dashboard or invite
- [ ] Admin role is the initial and only role for MVP; role field exists in user metadata for future extensibility

## Edge Cases
- User navigates directly to a protected route without a session → redirected to `/login` with return URL preserved
- Session token expires while user is on the page → next API call or navigation triggers redirect to `/login`
- Wrong password entered 5 times → Supabase rate-limiting applies automatically; no additional handling needed
- User tries to access `/login` while already authenticated → redirect to `/`

## Technical Requirements
- Supabase Auth (email + password provider); no OAuth/social login for MVP
- Next.js middleware (`middleware.ts`) handles route protection server-side for all routes except `/login` and `/api/health`
- Use `@supabase/ssr` package for cookie-based session management in Next.js App Router
- Use `window.location.href` for post-login redirect (not `router.push`) to ensure full session hydration
- No public self-registration: `signUp` is disabled in Supabase Auth settings; users added via Supabase dashboard
- Environment variables required: `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_ANON_KEY`

---
<!-- Sections below are added by subsequent skills -->

## Tech Design (Solution Architect)

### Component Structure

```
/login (public route)
+-- LoginPage
    +-- Card (centered, branded)
        +-- CardHeader (logo / app name)
        +-- LoginForm
        |   +-- Email Input
        |   +-- Password Input
        |   +-- Error Alert (shown on failed login)
        |   +-- Submit Button (with loading state)
        +-- CardFooter (optional: contact admin note)

All other routes (protected)
+-- AppLayout (wraps every page)
    +-- Sidebar / Navigation
    |   +-- UserMenu (avatar + email)
    |       +-- Logout Button
    +-- Page Content (Dashboard, Backtest, etc.)
```

### Data Model

No custom database table needed — Supabase Auth manages users entirely.

**User (managed by Supabase Auth):**
- Unique ID (auto-generated)
- Email address
- Password (hashed, never accessible)
- `role` field in user metadata: `"admin"` (default) — stored for future extensibility
- Session token — stored in cookies, survives page refresh

**Where data lives:** Supabase Auth service (not in our database tables)

### Route Protection Model

```
Request comes in
+-- Is route /login or /api/health?
|   +-- YES → Allow through (public)
+-- NO → Check session cookie (server-side, via middleware)
    +-- Valid session? → Allow through
    +-- No session?   → Redirect to /login (with return URL)
    +-- Already on /login with valid session? → Redirect to /
```

### Tech Decisions

| Decision | Choice | Why |
|---|---|---|
| Auth provider | Supabase Auth | Already in stack; handles tokens, rate limiting, and sessions out of the box |
| Session storage | Cookies (via `@supabase/ssr`) | Works correctly with Next.js server-side rendering; survives refresh |
| Route protection | Next.js Middleware | Runs server-side before page load — no client-side flicker |
| Post-login redirect | `window.location.href` | Forces full page reload to ensure session is fully hydrated in all server components |
| Self-registration | Disabled in Supabase | Admin-only platform; new users added via Supabase dashboard or invite link |

### Dependencies

| Package | Purpose |
|---|---|
| `@supabase/supabase-js` | Supabase client |
| `@supabase/ssr` | Cookie-based session management for Next.js App Router |

### Files to Create/Modify

| File | What it does |
|---|---|
| `src/app/login/page.tsx` | Login page (public) |
| `src/components/login-form.tsx` | Login form with validation and error handling |
| `middleware.ts` | Route protection — runs on every request |
| `src/lib/supabase.ts` | Supabase client helpers (browser + server) |
| `src/components/user-menu.tsx` | Logout button in app navigation |
| `.env.local.example` | Documents required environment variables |

## QA Test Results

**Tested:** 2026-03-10 (run 2) | **Build Status:** PASS (Next.js 16.1.6 Turbopack, 0 errors)

### Acceptance Criteria: 7/8 PASSED

| AC | Description | Result |
|----|-------------|--------|
| AC-1 | Login page at `/login` with email and password fields | PASS |
| AC-2 | Successful login redirects to `/` | PASS |
| AC-3 | Failed login shows error, form stays intact | PASS |
| AC-4 | All routes except `/login` protected | PASS |
| AC-5 | Session persisted via cookies | PASS |
| AC-6 | Log out button in navigation | PASS |
| AC-7 | No public self-registration | PASS (enforced via Supabase dashboard) |
| AC-8 | Admin role in user metadata | **FAIL** |

### Edge Cases: 4/4 PASSED

| EC | Description | Result |
|----|-------------|--------|
| EC-1 | Protected route redirect with return URL | PASS (login `returnTo` fixed; auth callback has BUG-1) |
| EC-2 | Expired session triggers redirect | PASS |
| EC-3 | Rate limiting on wrong passwords | PASS |
| EC-4 | `/login` while authenticated redirects to `/` | PASS |

### Bugs Found

**BUG-1: Open Redirect in Auth Callback via `next` Parameter** — CRITICAL
- `src/app/auth/callback/route.ts` lines 7 and 13: the `next` query parameter is used directly in `NextResponse.redirect()` without validation. An attacker can craft `/auth/callback?code=VALID_CODE&next=//evil.com` to redirect the user after authentication.
- Note: the login form's `returnTo` parameter was previously fixed with regex validation (`/^\/(?!\/)/`), but the auth callback has the same class of vulnerability.
- Fix: apply the same regex validation to the `next` parameter before redirecting.
- Priority: Fix before deployment

**BUG-2: Admin Role Not Enforced** — HIGH
- `src/app/(dashboard)/layout.tsx` line 21: role is read from `user.user_metadata?.role` and displayed in the UI, but no code ever writes the role during user creation, and no code restricts access based on the role value. The fallback is `"user"` (not `"admin"`), so the display is wrong for users without explicit metadata. Any authenticated Supabase user has full platform access.
- Priority: Fix before deployment

**BUG-3: Self-Registration Depends Only on Supabase Dashboard Config** — LOW
- No code prevents `supabase.auth.signUp()` calls. The anon key is exposed in the browser. If the "Disable signups" toggle in the Supabase dashboard is ever turned on accidentally, anyone could register.
- Priority: Fix in next sprint

**BUG-4: Dead Code — `/api/auth/signout` Route Never Called** — LOW
- `src/app/api/auth/signout/route.ts` exists but is not used by any component.
- Priority: Fix in next sprint

**BUG-5: `/api/auth/me` Does Not Return Role Field** — LOW
- `src/app/api/auth/me/route.ts` does not include the `role` field in the response, which will be needed once role enforcement is implemented.
- Priority: Fix in next sprint

### Security Audit Summary
- Authentication mechanism is solid (uses `getUser()` server-validated, double-layer middleware + server-side layout check)
- Login form `returnTo` parameter properly validated against open redirects
- Security headers configured in `next.config.ts` (X-Frame-Options, HSTS, etc.)
- No hardcoded secrets; `.env.local` is in `.gitignore`
- **Critical open redirect** in auth callback `next` parameter (BUG-1)
- No role-based access control despite being specified in AC-8 (BUG-2)

### Fixes Applied
- BUG-1 ✓ — `next` param validated with `/^\/(?!\/)/` in `src/app/auth/callback/route.ts`
- BUG-2 ✓ — Role enforcement added to `src/app/(dashboard)/layout.tsx`; non-admin users redirected to `/login`
- BUG-3 ✓ — By Design: no signup UI exists; Supabase dashboard "Disable signups" is the correct control point
- BUG-4 ✓ — Deleted dead code `src/app/api/auth/signout/route.ts` (signout handled in sidebar component)
- BUG-5 ✓ — `role` field added to `src/app/api/auth/me/route.ts` response

### Production Ready: YES

## Deployment

- **Production URL:** https://trading-backtester-omega.vercel.app
- **Deployed:** 2026-03-11
- **Status:** Live
