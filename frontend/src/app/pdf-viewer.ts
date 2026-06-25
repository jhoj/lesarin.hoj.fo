import {
  Component,
  ElementRef,
  HostListener,
  computed,
  effect,
  inject,
  input,
  output,
  signal,
  untracked,
  viewChild,
} from '@angular/core';
import * as pdfjsLib from 'pdfjs-dist';

// Served at the site root by the asset rule in angular.json (and by FastAPI in prod).
pdfjsLib.GlobalWorkerOptions.workerSrc = '/pdf.worker.min.mjs';

/** A box to draw over the page, in PDF points (top-left origin). */
export interface ViewBox {
  id: string;
  page: number;
  bbox: number[]; // [x0, top, x1, bottom]
  kind: 'value' | 'suggestion';
  label: string;
}

interface Rect {
  left: number;
  topPx: number;
  width: number;
  height: number;
}

interface DragState {
  id: string;
  mode: 'move' | 'resize';
  rect: Rect;
  startX: number;
  startY: number;
  orig: Rect;
}

@Component({
  selector: 'app-pdf-viewer',
  templateUrl: './pdf-viewer.html',
  styleUrl: './pdf-viewer.css',
})
export class PdfViewer {
  readonly pdfUrl = input<string | null>(null);
  readonly boxes = input<ViewBox[]>([]);
  readonly selectedId = input<string | null>(null);

  readonly boxMove = output<{ id: string; page: number; bbox: number[] }>();
  readonly boxSelect = output<string>();

  private readonly host = inject<ElementRef<HTMLElement>>(ElementRef);
  private readonly canvas = viewChild<ElementRef<HTMLCanvasElement>>('canvas');

  readonly page = signal(1);
  readonly totalPages = signal(1);
  readonly scale = signal(1);
  private readonly docVersion = signal(0);
  private readonly dragging = signal<DragState | null>(null);

  private pdfDoc: pdfjsLib.PDFDocumentProxy | null = null;
  private renderTask: pdfjsLib.RenderTask | null = null;

  constructor() {
    effect(() => {
      const url = this.pdfUrl();
      untracked(() => {
        if (url) this.loadDoc(url);
      });
    });
    effect(() => {
      this.page();
      this.docVersion();
      // Track the viewChild so this re-runs once the @if-gated canvas resolves —
      // otherwise the first render fires before the canvas exists and bails.
      const canvasRef = this.canvas();
      if (!canvasRef) return;
      untracked(() => void this.render());
    });
  }

  private async loadDoc(url: string): Promise<void> {
    this.dragging.set(null);
    try {
      const task = pdfjsLib.getDocument(url);
      this.pdfDoc = await task.promise;
      this.totalPages.set(this.pdfDoc.numPages);
      this.page.set(1);
      this.docVersion.update((v) => v + 1);
    } catch {
      this.pdfDoc = null;
    }
  }

  private async render(): Promise<void> {
    const doc = this.pdfDoc;
    const canvasRef = this.canvas();
    if (!doc || !canvasRef) return;
    const pageNum = this.page();
    if (pageNum < 1 || pageNum > doc.numPages) return;

    const page = await doc.getPage(pageNum);
    const available = this.host.nativeElement.clientWidth || 820;
    const base = page.getViewport({ scale: 1 });
    const scale = Math.max(0.4, Math.min(1.8, (available - 24) / base.width));
    const viewport = page.getViewport({ scale });

    const canvas = canvasRef.nativeElement;
    canvas.width = viewport.width;
    canvas.height = viewport.height;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    this.renderTask?.cancel();
    this.renderTask = page.render({ canvasContext: ctx, viewport });
    try {
      await this.renderTask.promise;
    } catch {
      /* superseded render — ignore */
    }
    this.scale.set(scale);
  }

  private toRect(bbox: number[], s: number): Rect {
    return {
      left: bbox[0] * s,
      topPx: bbox[1] * s,
      width: (bbox[2] - bbox[0]) * s,
      height: (bbox[3] - bbox[1]) * s,
    };
  }

  readonly pageBoxes = computed(() => {
    const s = this.scale();
    const pg = this.page();
    const drag = this.dragging();
    const sel = this.selectedId();
    return this.boxes()
      .filter((b) => b.page === pg && b.bbox && b.bbox.length === 4)
      .map((b) => {
        const rect = drag && drag.id === b.id ? drag.rect : this.toRect(b.bbox, s);
        return { ...b, ...rect, selected: b.id === sel };
      });
  });

  onBoxDown(ev: PointerEvent, box: { id: string; bbox: number[] }, mode: 'move' | 'resize'): void {
    ev.preventDefault();
    ev.stopPropagation();
    this.boxSelect.emit(box.id);
    const rect = this.toRect(box.bbox, this.scale());
    this.dragging.set({
      id: box.id,
      mode,
      rect: { ...rect },
      startX: ev.clientX,
      startY: ev.clientY,
      orig: { ...rect },
    });
  }

  @HostListener('window:pointermove', ['$event'])
  onPointerMove(ev: PointerEvent): void {
    const d = this.dragging();
    if (!d) return;
    const dx = ev.clientX - d.startX;
    const dy = ev.clientY - d.startY;
    const rect: Rect =
      d.mode === 'move'
        ? { left: d.orig.left + dx, topPx: d.orig.topPx + dy, width: d.orig.width, height: d.orig.height }
        : {
            left: d.orig.left,
            topPx: d.orig.topPx,
            width: Math.max(8, d.orig.width + dx),
            height: Math.max(6, d.orig.height + dy),
          };
    this.dragging.set({ ...d, rect });
  }

  @HostListener('window:pointerup')
  onPointerUp(): void {
    const d = this.dragging();
    if (!d) return;
    const s = this.scale() || 1;
    const bbox = [
      d.rect.left / s,
      d.rect.topPx / s,
      (d.rect.left + d.rect.width) / s,
      (d.rect.topPx + d.rect.height) / s,
    ];
    this.dragging.set(null);
    this.boxMove.emit({ id: d.id, page: this.page(), bbox });
  }

  prev(): void {
    this.page.update((p) => Math.max(1, p - 1));
  }

  next(): void {
    this.page.update((p) => Math.min(this.totalPages(), p + 1));
  }
}
