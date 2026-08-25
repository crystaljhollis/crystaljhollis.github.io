const syntheticRecord = `HEADLINE: Community Technology Workshop
DESCRIPTION: Adults and teens collaborate around laptops during a free neighborhood coding workshop.
BATCH KEYWORDS: community learning; laptops; coding; workshop; digital skills
DESCRIPTION WRITER: Demo Archive Team
SUBLOCATION: Riverside Learning Lab
CITY: Cedar Grove
STATE/PROVINCE: TX
COUNTRY: United States
TITLE: Community Technology Workshop
COLLECTION NUMBER: 2026-041
BATCH NUMBER: 003`;

const intakeRecord = document.querySelector('#intake-record');
const parseButton = document.querySelector('#parse-fields');
const loadButton = document.querySelector('#load-example');
const runButton = document.querySelector('#run-demo');
const parseStatus = document.querySelector('#parse-status');
const runStatus = document.querySelector('#run-status');
const progressBar = document.querySelector('#progress-bar');
const results = document.querySelector('#demo-results');

const fieldMap = {
  HEADLINE: '#field-headline',
  TITLE: '#field-title',
  DESCRIPTION: '#field-description',
  'BATCH KEYWORDS': '#field-keywords',
  CITY: '#field-city',
  'STATE/PROVINCE': '#field-state',
  'COLLECTION NUMBER': '#field-collection',
  'BATCH NUMBER': '#field-batch'
};

function parseRecord() {
  const labels = Object.keys(fieldMap);
  const escaped = labels.map((label) => label.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'));
  const pattern = new RegExp(`^(${escaped.join('|')}):\\s*(.*?)(?=^(${escaped.join('|')}):|$)`, 'gms');
  const parsed = {};
  for (const match of intakeRecord.value.matchAll(pattern)) {
    parsed[match[1]] = match[2].trim().replace(/\n+/g, ' ');
  }

  let populated = 0;
  Object.entries(fieldMap).forEach(([label, selector]) => {
    const input = document.querySelector(selector);
    input.value = parsed[label] || '';
    if (parsed[label]) populated += 1;
  });

  parseStatus.textContent = populated
    ? `Parsed ${populated} synthetic fields. Review them before running the demonstration.`
    : 'No recognized labels were found. Reload the synthetic example and try again.';
  return populated;
}

function loadExample() {
  intakeRecord.value = syntheticRecord;
  results.hidden = true;
  progressBar.style.width = '0%';
  runStatus.textContent = 'Ready. Nothing runs until you choose the demonstration.';
  parseStatus.textContent = 'The sample is ready to parse.';
  parseRecord();
}

function addTags(target, values) {
  target.replaceChildren(...values.map((value) => {
    const item = document.createElement('li');
    item.textContent = value;
    return item;
  }));
}

function finishDemo() {
  const headline = document.querySelector('#field-headline').value.trim();
  const title = document.querySelector('#field-title').value.trim();
  const description = document.querySelector('#field-description').value.trim();
  const keywords = document.querySelector('#field-keywords').value
    .split(/[;,]/)
    .map((item) => item.trim())
    .filter(Boolean);

  document.querySelector('#alt-result').textContent = document.querySelector('#option-alt').checked
    ? 'Six people sit and stand around laptops beneath a Community Technology Workshop sign in a bright learning space.'
    : 'Alt-text drafting was not selected for this run.';

  const approved = document.querySelector('#option-keywords').checked
    ? keywords.filter((keyword) => ['community learning', 'laptops', 'coding', 'workshop', 'digital skills'].includes(keyword.toLowerCase()))
    : [];
  const review = document.querySelector('#option-keywords').checked
    ? ['collaboration', 'name badges']
    : ['Keyword suggestions were not selected.'];
  addTags(document.querySelector('#approved-result'), approved.length ? approved : ['No suggestions requested']);
  addTags(document.querySelector('#review-result'), review);

  const output = {
    'XMP-dc:Title': title,
    'IPTC:Headline': headline,
    'XMP-dc:Description': description,
    'XMP-dc:Subject': keywords,
    'XMP-photoshop:TransmissionReference': `COLL-${document.querySelector('#field-collection').value}, BATCH-${document.querySelector('#field-batch').value}`,
    'XMP-iptcCore:AltTextAccessibility': document.querySelector('#option-alt').checked ? 'Draft pending human approval' : 'Not requested'
  };
  document.querySelector('#metadata-output').textContent = JSON.stringify(output, null, 2);

  const logItems = [
    'Applied the selected generic template and structured metadata to 3 simulated images.',
    document.querySelector('#option-alt').checked ? 'Generated accessibility alt-text drafts for human review.' : 'Skipped accessibility alt-text drafting.',
    document.querySelector('#option-keywords').checked ? 'Compared optional keyword suggestions with the synthetic approved list.' : 'Skipped optional keyword suggestions.',
    'Completed the simulated batch with 0 failures. No files were changed.'
  ];
  const log = document.querySelector('#demo-log-list');
  log.replaceChildren(...logItems.map((text) => {
    const item = document.createElement('li');
    item.textContent = text;
    return item;
  }));

  results.hidden = false;
  progressBar.style.width = '100%';
  runStatus.textContent = 'Synthetic demonstration complete. Review-only AI outputs remain separate from approved metadata.';
  results.scrollIntoView({ behavior: 'smooth', block: 'start' });
  runButton.disabled = false;
}

function runDemo() {
  if (!document.querySelector('#field-headline').value.trim() && !parseRecord()) {
    return;
  }

  runButton.disabled = true;
  results.hidden = true;
  progressBar.style.width = '35%';
  runStatus.textContent = 'Applying the generic metadata template to simulated images...';

  window.setTimeout(() => {
    progressBar.style.width = '70%';
    runStatus.textContent = 'Preparing accessibility and keyword drafts for review...';
  }, 250);
  window.setTimeout(finishDemo, 650);
}

loadButton.addEventListener('click', loadExample);
parseButton.addEventListener('click', parseRecord);
runButton.addEventListener('click', runDemo);
loadExample();
