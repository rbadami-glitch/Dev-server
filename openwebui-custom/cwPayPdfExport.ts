import { decode } from 'html-entities';
import { marked, type Token, type Tokens } from 'marked';

const cellText = (cell: Tokens.TableCell): string =>
	decode(cell.tokens.map((part) => ('text' in part ? part.text : '')).join(''));

const tableRows = (token: Tokens.Table): string[][] => [
	token.header.map((cell) => decode(cell.text)),
	...token.rows.map((row) => row.map(cellText))
];

const plainText = (value = ''): string =>
	decode(value)
		.replace(/!\[([^\]]*)\]\([^)]*\)/g, '$1')
		.replace(/\[([^\]]+)\]\([^)]*\)/g, '$1')
		.replace(/[`*_~>#]/g, '')
		.replace(/\s+/g, ' ')
		.trim();

const tokenText = (token: Token): string => ('text' in token ? token.text : '');

/** Creates a PDF for a complete CW Pay Markdown response, including any result tables. */
export const cwPayResultPdfBlob = async (content: string): Promise<Blob> => {
	// PDF libraries load only after the user requests an export.
	const [{ default: jsPDF }, { default: autoTable }] = await Promise.all([
		import('jspdf'),
		import('jspdf-autotable')
	]);

	const pdf = new jsPDF({ orientation: 'landscape', unit: 'pt', format: 'a4' });
	const margin = 42;
	const pageWidth = pdf.internal.pageSize.getWidth();
	const pageHeight = pdf.internal.pageSize.getHeight();
	const maxY = pageHeight - 42;
	let cursorY = 38;

	const startPage = () => {
		pdf.addPage();
		cursorY = margin;
	};

	const writeText = (value: string, fontSize = 10, bold = false) => {
		const text = plainText(value);
		if (!text) return;

		pdf.setFont('helvetica', bold ? 'bold' : 'normal');
		pdf.setFontSize(fontSize);
		pdf.setTextColor(17, 24, 39);
		const lineHeight = fontSize * 1.35;
		const lines = pdf.splitTextToSize(text, pageWidth - margin * 2) as string[];

		for (const line of lines) {
			if (cursorY + lineHeight > maxY) startPage();
			pdf.text(line, margin, cursorY);
			cursorY += lineHeight;
		}
		cursorY += 7;
	};

	pdf.setProperties({ title: 'CW Pay result', creator: 'Open WebUI' });
	pdf.setFont('helvetica', 'bold');
	pdf.setFontSize(16);
	pdf.setTextColor(17, 24, 39);
	pdf.text('CW Pay result', margin, cursorY);
	cursorY += 18;
	pdf.setFont('helvetica', 'normal');
	pdf.setFontSize(8);
	pdf.setTextColor(100, 100, 100);
	pdf.text(`Exported ${new Date().toLocaleString()}`, margin, cursorY);
	cursorY += 22;

	for (const token of marked.lexer(content)) {
		if (token.type === 'table') {
			const [head, ...body] = tableRows(token);
			if (cursorY > pageHeight - 100) startPage();
			autoTable(pdf, {
				head: [head],
				body,
				startY: cursorY,
				margin: { top: margin, right: margin, bottom: 42, left: margin },
				showHead: 'everyPage',
				horizontalPageBreak: true,
				horizontalPageBreakRepeat: 0,
				rowPageBreak: 'avoid',
				theme: 'grid',
				styles: {
					font: 'helvetica',
					fontSize: 9,
					cellPadding: 5,
					overflow: 'linebreak',
					valign: 'top'
				},
				headStyles: { fillColor: [31, 41, 55], textColor: [255, 255, 255] },
				alternateRowStyles: { fillColor: [248, 250, 252] }
			});
			cursorY = (pdf as unknown as { lastAutoTable: { finalY: number } }).lastAutoTable.finalY + 18;
			continue;
		}

		if (token.type === 'heading') {
			writeText(token.text, Math.max(12, 18 - token.depth * 2), true);
		} else if (token.type === 'list') {
			for (const item of token.items) writeText(`• ${item.text}`);
		} else {
			writeText(tokenText(token));
		}
	}

	const pageCount = pdf.internal.getNumberOfPages();
	for (let page = 1; page <= pageCount; page++) {
		pdf.setPage(page);
		pdf.setFont('helvetica', 'normal');
		pdf.setFontSize(8);
		pdf.setTextColor(100, 100, 100);
		pdf.text(`${page} / ${pageCount}`, pageWidth - margin, pageHeight - 20, { align: 'right' });
	}

	return pdf.output('blob');
};


