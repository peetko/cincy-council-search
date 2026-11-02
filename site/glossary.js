/* Plain-language explanations shown in the site's hover / tap tooltips.
 * Edit freely: each entry is  key: ["Title", "Explanation"].
 * Facts about Council procedure come from WVXU's guides (linked in the site footer)
 * and the City Charter as those guides describe it.
 */
window.GLOSSARY = {
  // ---- kinds of items
  ordinance: ["Ordinance", "A local law that becomes part of the Cincinnati Municipal Code. It can cover only one subject. Most need 5 of Council's 9 votes and take effect 30 days after the mayor signs them."],
  resolution: ["Resolution", "A statement of Council's position, such as recognizing a person or group, or taking a stance on state or federal action. Simple resolutions aren't legally binding and skip committee. “Legislative resolutions” (like certifying property-tax rates) must pass a committee first."],
  motion: ["Motion", "Council directing the City administration: asking for a report, starting a policy, or signaling it wants to fund something, which prompts the Law Department to draft an ordinance."],
  communication: ["Communication or report", "Reports from the administration, letters, lobbyist (legislative agent) registrations and similar documents filed with the Clerk of Council. Most are marked “File”: shared for information, with no vote."],
  appointment: ["Appointment", "The mayor's picks for City boards and commissions, sent to Council for its advice and consent."],
  presentation: ["Presentation", "A briefing to a committee, often by City staff or an outside group. No vote is taken."],
  statement: ["Statement", "A written statement entered into the record, often attached to an item. No vote is taken."],
  other: ["Minutes note", "Other text from the agenda or minutes, such as attendance or discussion notes, with no legislation attached."],

  // ---- outcomes
  passed: ["Passed", "Approved by Council. Ordinances go to the mayor to sign. If the mayor doesn't act within five days, an ordinance takes effect anyway. A veto can be overridden with 6 votes (vetoes are rare)."],
  passed_emergency: ["Passed as an emergency", "Passed with an emergency clause, so it takes effect as soon as the mayor signs instead of 30 days later. The emergency clause needs 6 of 9 votes and must state a reason. Emergency ordinances can't be overturned by a voter referendum the way regular ones can."],
  failed: ["Failed", "The item did not get enough votes."],
  referred: ["Referred to committee", "The mayor sends each new item to a committee. Ordinances, motions and legislative resolutions must win a majority there before the full Council votes. Many items on a Council agenda are only being referred that day."],
  held: ["Held", "The item was put off to a later meeting instead of being voted on."],
  filed: ["Filed", "Received for the record with no vote needed. This is common for reports, special-event permit applications and other FYI items."],
  scheduled: ["On an upcoming agenda", "Listed for a meeting that hasn't happened yet."],

  // ---- procedure
  three_readings: ["Three readings", "The City Charter says an ordinance must be read on three different days. Council nearly always votes to skip that rule (it takes 7 votes), but only after the item has already gone through committee."],
  final_vote: ["Final-passage vote", "Ordinances usually get separate roll calls: one to suspend the three-readings rule, one to pass, and sometimes one on the emergency clause. These stats count only the vote to pass or adopt."],
  file_number: ["File number", "Legistar's ID for a piece of legislation: the year plus a sequence number. The same number follows the item from introduction through committee to the final vote."],
  council: ["City Council", "Nine members elected citywide to two-year terms. Full Council meets most Wednesdays at 2 p.m. at City Hall; the mayor runs the meeting and calls the votes."],
  committee: ["Committees", "Smaller groups of Council members where most debate happens: members ask questions, hear from the public and staff, and propose amendments. Passing a committee takes a majority of its members."],
  sponsor: ["Sponsor", "Who submitted the item: one or more Council members, the mayor, or the City Manager (on behalf of City departments)."],
  department: ["Department", "City departments named in the item's text, found by matching keywords. One item can mention several."],
  outcome: ["Outcome", "What happened to the item at that meeting: passed, referred to committee, held, filed, and so on."],
  vote_filter: ["No votes", "Show only items where at least one member voted no or abstained on final passage, or narrow it to one member to see what they opposed."],
  dissent: ["Split vote", "Not everyone voted yes on final passage. Most Council votes are unanimous, so these are where members disagreed."],
  relevance: ["Sort by relevance", "Puts items where your words appear in the title or file number first, instead of newest first."],

  // ---- stats
  kpi_ordinances: ["Ordinances passed", "Distinct ordinances (local laws) passed at full Council so far this year, compared with the same days last year. Most need 5 of 9 votes; many pass “as an emergency” so they take effect right away."],
  kpi_resolutions: ["Resolutions adopted", "Statements of Council's position adopted so far this year, such as recognitions and stances on state or federal issues. Simple resolutions aren't legally binding."],
  kpi_motions: ["Motions introduced", "Requests or directions to the City administration put before full Council so far this year. A motion often leads to a report or to an ordinance drafted by the Law Department."],
  kpi_meetings: ["Meetings with minutes", "Full Council and committee meetings this year whose minutes have been published on Legistar."],
  kpi_present: ["Average members present", "On each final-passage vote, how many of the 9 members cast a vote (yes, no or abstain), averaged over the year. Excused absences count as absent."],
  kpi_unanimous: ["Unanimous final votes", "Share of final-passage votes with no “no” votes. Council usually votes together; click to see the split votes."],
  chart_monthly: ["Ordinances per month", "Distinct ordinances passed at full Council each month, this year against last year. The current month counts only the days so far, compared with the same days a year ago."],
  no_votes_month: ["No votes this month", "Full Council passed no ordinances this month in either year. Council usually takes a summer break in July after passing the City budget in June, which is why June is so busy."],
  chart_topics: ["Topics", "Items are tagged with topics by keyword (for example, “streetcar” means Transit & streets). Each piece of legislation counts once even if it appears at several meetings. Compared with the same days a year earlier."],
  chart_members: ["How members voted", "Final-passage roll calls only. Votes to suspend the three readings or on an emergency clause aren't counted."],
  col_present: ["Present", "Share of final-passage votes where the member was there and voted (yes, no or abstain)."],
  col_dissent: ["No / abstain", "Final-passage votes where the member voted no or abstained. Click a number to see what those votes were about."],
  col_against: ["Against majority", "Votes where the member voted the opposite way from most colleagues, a rough measure of independent votes."],
  spark: ["Mentions per quarter", "How many agenda and minutes items matched your search in each quarter, to show when a subject was most active."],
};
