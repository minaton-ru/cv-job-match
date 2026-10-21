# CV Job Match

Watches the careers sites of selected companies for new Vacancies, assesses each one against a single Candidate's Profile, and on request tailors the Candidate's resume and cover letter to a chosen Vacancy.

## Language

### Discovery

**Company**:
An employer whose own corporate careers site is monitored.
_Avoid_: Employer, site

**Source**:
One search or listing on a Company's own careers site that is checked for Vacancies. A Company can have several Sources.
_Avoid_: Site, website, page, feed

**Vacancy**:
One job opening at one Company, as published on that Company's own careers site. Identified by its Company plus the site's own ID for it; finding it through two Sources does not make it two Vacancies.
_Avoid_: Job, posting, opening, position

**Revision**:
A change to a known Vacancy that is visible in its Source's list. It is still the same Vacancy. A Revision is recorded but never triggers a new Assessment.

**Baseline Run**:
The first successful Scrape Run of a new Source. Every Vacancy it finds is recorded as already seen, without being assessed.

**Closed Vacancy**:
A Vacancy that no Source of its Company still lists, judged by each Source's latest successful Scrape Run. It is kept, not deleted. If it reappears it is reopened as the same Vacancy and keeps its status.

**Scrape Run**:
One check of one Source, together with its outcome.

**Suspect Scrape Run**:
A Scrape Run whose list fetch failed, or that found nothing when the last successful run of that Source found Vacancies. It never closes Vacancies.

### Evaluation

**Candidate**:
The one person this service works for.
_Avoid_: User, applicant

**Candidate Profile**:
The Candidate's master resume plus their criteria: preferences, excluded Roles, strengths and weaknesses.
_Avoid_: Profile settings, config

**Role**:
A kind of job, such as "Backend Engineer", as used in the Candidate's criteria. A Role is not a Vacancy.
_Avoid_: Position, job type

**Pre-filter**:
Cheap rule-based screening of a Vacancy (title, city, language) against the Candidate's criteria, with no AI involved. Vacancies it rejects are kept, marked as filtered out together with the reason.

**Assessment**:
An AI judgement of how well a Vacancy fits the Candidate Profile. It consists of a score, a verdict (apply, maybe or skip), the strengths that match, the gaps, and any exclusions triggered. It is tied to the version of the Profile it was made against.
_Avoid_: Score, match, evaluation

### Decision and tailoring

**Shortlisted Vacancy**:
A Vacancy the Candidate has asked to have Tailoring done for.

**Applied Vacancy**:
A Vacancy the Candidate has marked as applied to on the Company's site.

**Dismissed Vacancy**:
A Vacancy the Candidate has decided not to pursue. Its Revisions do not bring it back to the Candidate's attention.

**Tailoring**:
Producing an Application Pack for one Vacancy from the Candidate Profile. It happens only when the Candidate asks for it. It may select, reorder and reword facts from the master resume, but never add new ones.

**Application Pack**:
The resume and cover letter produced by Tailoring for one Vacancy.
_Avoid_: Documents, output, CV bundle
