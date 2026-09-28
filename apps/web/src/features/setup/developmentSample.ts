const sampleLines = [
  "Development-only ethanol protocol - deterministic fake mode sample",
  "Preparation",
  "1. Add 10 mL ethanol to the demonstration vessel.",
  "2. Incubate the sample for 5 minutes.",
  "3. Warning: do not open the vessel; record the temperature.",
];

function escapePdfText(value: string): string {
  return value.replaceAll("\\", "\\\\").replaceAll("(", "\\(").replaceAll(")", "\\)");
}

export function developmentProtocolFile(): File {
  const textCommands = sampleLines
    .map((line, index) => `${index === 0 ? "" : "T*\n"}(${escapePdfText(line)}) Tj`)
    .join("\n");
  const stream = `BT\n/F1 12 Tf\n17 TL\n72 740 Td\n${textCommands}\nET`;
  const objects = [
    "<< /Type /Catalog /Pages 2 0 R >>",
    "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
    "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
    "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    `<< /Length ${stream.length} >>\nstream\n${stream}\nendstream`,
  ];

  let pdf = "%PDF-1.4\n";
  const offsets = [0];
  objects.forEach((object, index) => {
    offsets.push(pdf.length);
    pdf += `${index + 1} 0 obj\n${object}\nendobj\n`;
  });
  const crossReferenceOffset = pdf.length;
  pdf += `xref\n0 ${objects.length + 1}\n0000000000 65535 f \n`;
  pdf += offsets
    .slice(1)
    .map((offset) => `${offset.toString().padStart(10, "0")} 00000 n \n`)
    .join("");
  pdf += `trailer\n<< /Size ${objects.length + 1} /Root 1 0 R >>\nstartxref\n${crossReferenceOffset}\n%%EOF\n`;

  return new File([pdf], "development-only-ethanol-protocol.pdf", {
    type: "application/pdf",
  });
}
