# Acme Corp Security and Access Policy (Excerpt)

## Password Requirements

All account passwords must be at least **14 characters** long and must not be reused
from any previous password or from personal accounts. Passwords expire every
**12 months**. Multi-factor authentication is mandatory on every company system,
including email, VPN, and the HR portal. Phishing-resistant authenticator apps are
preferred over SMS codes.

Password managers approved by IT (currently the Acme Vault client) must be used for
storing credentials. Writing passwords on paper or storing them in browser defaults
is prohibited.

## VPN and Remote Access

Only company-managed devices may connect to the corporate VPN. Personal laptops and
phones are not permitted on the VPN under any circumstances. Devices must have disk
encryption enabled and the endpoint protection agent running before access is
granted.

VPN access is granted per-role through the access request form and is reviewed every
quarter. Contractors receive time-limited access that expires with their engagement
date. Split tunnelling is disabled on all profiles; all traffic from a VPN session is
routed through company infrastructure.

## Data Classification

Every document and dataset must carry one of three classification labels:

- **Public:** approved for external release (marketing material, public website).
- **Internal:** default for day-to-day work; may be shared with any employee.
- **Confidential:** customer data, financial records, security configurations; access
  is need-to-know and must be logged in the access register.

Confidential data must never be stored on personal cloud accounts or emailed outside
the company domain. Sharing Confidential data with third parties requires written
approval from both the data owner and the legal team.

## Device Security

Laptops must use full-disk encryption (BitLocker on Windows, FileVault on macOS).
Screen lock must engage after 5 minutes of inactivity. Removable media such as USB
drives are blocked by policy; exceptions require a documented business case approved
by IT security.

Lost or stolen devices must be reported to the IT service desk **within 1 hour** of
discovery, at any time of day. Remote wipe will be issued immediately for company
devices.

## Incident Reporting

Suspected phishing emails, unusual account activity, or data spills must be reported
to security@acme.example immediately. Do not delete suspicious messages; the security
team needs them for analysis. Security incidents are triaged within 2 hours during
business hours.

## Security Training

All employees complete security awareness training within 30 days of joining and
every **6 months** thereafter. Completion is tracked centrally; managers receive
escalations for overdue training after 14 days.
