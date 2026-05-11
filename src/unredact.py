import os
import fitz  # PyMuPDF
import glob
import argparse
import sys
import csv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# ===========================================================================
# PDF helpers (unchanged)
# ===========================================================================

def clean_vector_redactions(page):
    cleaned_drawings = []
    black_v_removed = 0
    white_v_removed = 0
    drawings = page.get_drawings()
    
    for path in drawings:
        is_redaction = False
        fill_color = path.get("fill")
        stroke_color = path.get("color")
        
        if fill_color:
            if all(c < 0.05 for c in fill_color):
                bbox = path["rect"]
                if bbox.width > 5 and bbox.height > 5:
                    is_redaction = True
                    black_v_removed += 1
        
        if not is_redaction and stroke_color:
            if all(c < 0.05 for c in stroke_color) and path.get("width", 0) > 10:
                is_redaction = True
                black_v_removed += 1

        if not is_redaction and fill_color:
            if all(c > 0.95 for c in fill_color):
                bbox = path["rect"]
                if bbox.width > 5 and bbox.height > 5:
                    is_redaction = True
                    white_v_removed += 1

        if not is_redaction:
            cleaned_drawings.append(path)
            
    return cleaned_drawings, black_v_removed, white_v_removed


def process_file(file_path, output_folder, remove_bbox, highlight_text, custom_name=None):
    base_fname = os.path.basename(file_path)
    fname_no_ext = os.path.splitext(base_fname)[0]
    
    if custom_name:
        final_name = custom_name if custom_name.lower().endswith(".pdf") else f"{custom_name}.pdf"
    else:
        final_name = f"{fname_no_ext}_UNREDACTED.pdf"

    stats = {"black_img": 0, "white_img": 0, "black_vec": 0, "white_vec": 0, "annots": 0}

    print(f"\n[STARTING] {base_fname} -> {final_name}")
    try:
        doc = fitz.open(file_path)
        total_pages = len(doc)
        new_doc = fitz.open() 
        
        for page_index, page in enumerate(doc):
            current_pg = page_index + 1
            print(f"  [PROCESSING] Page {current_pg} of {total_pages}...", end='\r')
            
            page.set_cropbox(page.rect)
            page.set_mediabox(page.rect)

            if remove_bbox == 1:
                page_annots = list(page.annots())
                stats["annots"] += len(page_annots)
                for annot in page_annots:
                    page.delete_annot(annot)

            new_page = new_doc.new_page(width=page.rect.width, height=page.rect.height)
            
            page_images = page.get_images(full=True)
            for img in page_images:
                xref = img[0]
                try:
                    img_rects = page.get_image_rects(xref)
                    if not img_rects: continue
                    target_rect = img_rects[0]
                    
                    if target_rect.height > 10:
                        pix = fitz.Pixmap(doc, xref)
                        should_keep = True
                        if remove_bbox == 1:
                            check_pix = fitz.Pixmap(fitz.csRGB, pix) if pix.colorspace.n > 3 else pix
                            pixels = check_pix.samples
                            avg_brightness = sum(pixels) / len(pixels)
                            
                            if avg_brightness < 15:
                                should_keep = False
                                stats["black_img"] += 1
                            if avg_brightness > 240:
                                should_keep = False
                                stats["white_img"] += 1
                            if check_pix != pix: check_pix = None

                        if should_keep:
                            new_page.insert_image(target_rect, pixmap=pix)
                        pix = None 
                except: continue

            if remove_bbox == 1:
                safe_drawings, b_rem, w_rem = clean_vector_redactions(page)
                stats["black_vec"] += b_rem
                stats["white_vec"] += w_rem
                shape = new_page.new_shape()
                for path in safe_drawings:
                    for item in path["items"]:
                        if item[0] == "l": shape.draw_line(item[1], item[2])
                        elif item[0] == "re": shape.draw_rect(item[1])
                        elif item[0] == "qu": shape.draw_quad(item[1])
                        elif item[0] == "c": shape.draw_bezier(item[1], item[2], item[3], item[4])
                    shape.finish(
                        fill=path.get("fill"), 
                        color=path.get("color"), 
                        width=path.get("width", 1),
                        fill_opacity=path.get("fill_opacity", 1)
                    )
                shape.commit()
            else:
                shape = new_page.new_shape()
                for path in page.get_drawings():
                    for item in path["items"]:
                        if item[0] == "l": shape.draw_line(item[1], item[2])
                        elif item[0] == "re": shape.draw_rect(item[1])
                    shape.finish(fill=path.get("fill"), color=path.get("color"))
                shape.commit()

            text_dict = page.get_text("dict")
            for block in text_dict["blocks"]:
                for line in block.get("lines", []):
                    for span in line.get("spans", []):
                        if span["text"].strip():
                            text_color = (1, 0, 0) if highlight_text == 1 else (0, 0, 0)
                            new_page.insert_text(
                                span["origin"], 
                                span["text"], 
                                fontsize=span["size"], 
                                color=text_color,
                                overlay=True,
                                render_mode=0,
                                fill_opacity=1.0
                            )

        summary = (f"\n  [SUMMARY] Removed: {stats['black_img']} BlackImg, {stats['white_img']} WhiteImg, "
                   f"{stats['black_vec']} BlackVec, {stats['white_vec']} WhiteVec, {stats['annots']} Annots")
        print(summary)
        out_path = os.path.join(output_folder, final_name)
        new_doc.save(out_path, garbage=3, deflate=False)
        doc.close()
        new_doc.close()
        print(f"Success: Saved to {out_path}")
        
        return [base_fname, stats['black_img'], stats['white_img'], stats['black_vec'], stats['white_vec'], stats['annots']]
        
    except Exception as e:
        print(f"\nError processing {base_fname}: {e}")
        return None


def is_pdf(file_path):
    return file_path.lower().endswith(".pdf")


def run_operation(input_path, output_folder, remove_bbox, highlight_text, custom_name, hits_only=False):
    if not os.path.exists(output_folder): 
        os.makedirs(output_folder)
    
    log_data = []
    files_to_process = []
    is_single_file = os.path.isfile(input_path)

    if is_single_file:
        files_to_process.append(input_path)
    elif os.path.isdir(input_path):
        files_to_process = glob.glob(os.path.join(input_path, "*.pdf"))

    for file_path in files_to_process:
        result = process_file(file_path, output_folder, remove_bbox, highlight_text, 
                              custom_name if is_single_file else None)
        
        if result:
            total_removed = sum(result[1:]) 
            
            if hits_only and total_removed == 0:
                cleanup_path = os.path.join(output_folder, result[0]) 
                if os.path.exists(cleanup_path):
                    os.remove(cleanup_path)
                print(f" No redactions found. Output discarded: {result[0]}")
            else:
                log_data.append(result)

    if log_data:
        csv_path = os.path.join(output_folder, "summary_of_changes.csv")
        headers = ["Filename", "Black_Images", "White_Images", "Black_Vectors", "White_Vectors", "Annotations"]
        with open(csv_path, "w", newline='') as f:
            writer = csv.writer(f)
            writer.writerow(headers)
            writer.writerows(log_data)
        print(f"\n Summary CSV saved to: {csv_path}")


# ===========================================================================
# VIDEO helpers
# ===========================================================================

VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".wmv", ".flv", ".webm", ".m4v"}

def is_video(file_path):
    return os.path.splitext(file_path)[1].lower() in VIDEO_EXTENSIONS


def detect_solid_redaction_mask(frame, black_thresh=15, white_thresh=240, min_area=500):
    """
    Detect solid black or white rectangular regions that look like redaction boxes.
    Returns a uint8 mask (255 = redaction pixel) and a list of bounding rects.
    
    Strategy:
      1. Convert to grayscale.
      2. Threshold to isolate near-black and near-white regions separately.
      3. Morphological close to merge adjacent pixels into blobs.
      4. Find contours and keep only those that are:
           - Larger than min_area pixels
           - Have a high "rectangularity" score (area / bounding_rect_area > 0.85)
             so we don't flag natural dark/light areas of the image.
    """
    import cv2
    import numpy as np

    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    h, w = gray.shape

    # --- Black mask ---
    _, black_mask = cv2.threshold(gray, black_thresh, 255, cv2.THRESH_BINARY_INV)
    # --- White mask ---
    _, white_mask = cv2.threshold(gray, white_thresh, 255, cv2.THRESH_BINARY)

    combined = cv2.bitwise_or(black_mask, white_mask)

    # Close small gaps
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
    combined = cv2.morphologyEx(combined, cv2.MORPH_CLOSE, kernel, iterations=2)

    contours, _ = cv2.findContours(combined, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    redaction_mask = np.zeros((h, w), dtype=np.uint8)
    boxes = []  # (x, y, w, h, color_type)

    for cnt in contours:
        area = cv2.contourArea(cnt)
        if area < min_area:
            continue

        x, y, bw, bh = cv2.boundingRect(cnt)
        rect_area = bw * bh
        if rect_area == 0:
            continue

        rectangularity = area / rect_area
        if rectangularity < 0.85:
            # Not rectangular enough — likely a natural dark/light region
            continue

        # Classify color
        roi = gray[y:y+bh, x:x+bw]
        mean_val = float(roi.mean())
        if mean_val <= black_thresh * 2:
            color_type = "black"
        elif mean_val >= white_thresh - 15:
            color_type = "white"
        else:
            # Mixed — skip; not a solid-color redaction
            continue

        cv2.rectangle(redaction_mask, (x, y), (x + bw, y + bh), 255, -1)
        boxes.append((x, y, bw, bh, color_type))

    return redaction_mask, boxes


def inpaint_frame(frame, mask):
    """Inpaint redacted regions using Navier-Stokes algorithm."""
    import cv2
    if mask.max() == 0:
        return frame  # Nothing to inpaint
    # Radius 5 gives good quality; increase for larger boxes (slower)
    return cv2.inpaint(frame, mask, inpaintRadius=5, flags=cv2.INPAINT_NS)


def seconds_to_timestamp(seconds):
    """Convert float seconds to HH:MM:SS.mmm string."""
    ms = int((seconds % 1) * 1000)
    s = int(seconds) % 60
    m = (int(seconds) // 60) % 60
    h = int(seconds) // 3600
    return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"


def process_video(file_path, output_folder, custom_name=None,
                  black_thresh=15, white_thresh=240, min_area=500,
                  hits_only=False):
    """
    Process a single video file:
      - Detect solid-color rectangular redactions per frame
      - Inpaint them
      - Write cleaned video
      - Return CSV row data: [filename, total_redacted_frames, first_ts, last_ts, event_count]
    """
    import cv2
    import numpy as np

    base_fname = os.path.basename(file_path)
    fname_no_ext = os.path.splitext(base_fname)[0]

    if custom_name:
        final_name = custom_name if any(custom_name.lower().endswith(e) for e in VIDEO_EXTENSIONS) \
                     else f"{custom_name}.mp4"
    else:
        final_name = f"{fname_no_ext}_UNREDACTED.mp4"

    print(f"\n[STARTING VIDEO] {base_fname} -> {final_name}")

    cap = cv2.VideoCapture(file_path)
    if not cap.isOpened():
        print(f"  [ERROR] Cannot open video: {file_path}")
        return None

    fps        = cap.get(cv2.CAP_PROP_FPS) or 25.0
    width      = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height     = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

    out_path = os.path.join(output_folder, final_name)
    fourcc   = cv2.VideoWriter_fourcc(*"mp4v")
    writer   = cv2.VideoWriter(out_path, fourcc, fps, (width, height))

    # Tracking
    redacted_frames   = 0
    frame_events      = []   # list of dicts per redaction event
    prev_had_redaction = False
    event_start_ts    = None
    event_boxes       = []
    frame_idx         = 0

    while True:
        ret, frame = cap.read()
        if not ret:
            break

        timestamp_sec = frame_idx / fps
        mask, boxes = detect_solid_redaction_mask(
            frame,
            black_thresh=black_thresh,
            white_thresh=white_thresh,
            min_area=min_area,
        )

        has_redaction = len(boxes) > 0

        if has_redaction:
            redacted_frames += 1
            cleaned = inpaint_frame(frame, mask)
            writer.write(cleaned)

            if not prev_had_redaction:
                # New redaction event starts
                event_start_ts = timestamp_sec
                event_boxes = boxes[:]
            else:
                event_boxes.extend(boxes)
        else:
            writer.write(frame)

            if prev_had_redaction:
                # Redaction event just ended
                event_end_ts = timestamp_sec
                black_count = sum(1 for b in event_boxes if b[4] == "black")
                white_count = sum(1 for b in event_boxes if b[4] == "white")
                frame_events.append({
                    "start": seconds_to_timestamp(event_start_ts),
                    "end":   seconds_to_timestamp(event_end_ts),
                    "black_boxes": black_count,
                    "white_boxes": white_count,
                })
                event_boxes = []

        prev_had_redaction = has_redaction
        frame_idx += 1

        if frame_idx % 100 == 0:
            pct = (frame_idx / total_frames * 100) if total_frames > 0 else 0
            print(f"  [PROCESSING] Frame {frame_idx}/{total_frames} ({pct:.1f}%) | "
                  f"Redacted frames so far: {redacted_frames}", end='\r')

    # Close any open event at end-of-file
    if prev_had_redaction and event_start_ts is not None:
        black_count = sum(1 for b in event_boxes if b[4] == "black")
        white_count = sum(1 for b in event_boxes if b[4] == "white")
        frame_events.append({
            "start": seconds_to_timestamp(event_start_ts),
            "end":   seconds_to_timestamp(frame_idx / fps),
            "black_boxes": black_count,
            "white_boxes": white_count,
        })

    cap.release()
    writer.release()

    if hits_only and redacted_frames == 0:
        os.remove(out_path)
        print(f"\n  No redactions found. Output discarded: {final_name}")
        return None

    first_ts = frame_events[0]["start"] if frame_events else "N/A"
    last_ts  = frame_events[-1]["end"]   if frame_events else "N/A"

    print(f"\n  [SUMMARY] {redacted_frames} redacted frames across {len(frame_events)} event(s). "
          f"First: {first_ts}  Last: {last_ts}")
    print(f"  Saved to: {out_path}")

    return {
        "filename":        base_fname,
        "redacted_frames": redacted_frames,
        "event_count":     len(frame_events),
        "first_timestamp": first_ts,
        "last_timestamp":  last_ts,
        "events":          frame_events,
    }


def run_video_operation(input_path, output_folder, custom_name=None,
                        black_thresh=15, white_thresh=240, min_area=500,
                        hits_only=False):
    """Orchestrate video processing and write CSV summary."""
    if not os.path.exists(output_folder):
        os.makedirs(output_folder)

    files_to_process = []
    is_single_file = os.path.isfile(input_path)

    if is_single_file:
        files_to_process.append(input_path)
    elif os.path.isdir(input_path):
        for ext in VIDEO_EXTENSIONS:
            files_to_process.extend(glob.glob(os.path.join(input_path, f"*{ext}")))
            files_to_process.extend(glob.glob(os.path.join(input_path, f"*{ext.upper()}")))

    if not files_to_process:
        print("No video files found at the given path.")
        return

    summary_rows   = []   # one row per file for the top-level CSV
    all_event_rows = []   # one row per redaction event across all files

    for file_path in files_to_process:
        result = process_video(
            file_path, output_folder,
            custom_name=custom_name if is_single_file else None,
            black_thresh=black_thresh,
            white_thresh=white_thresh,
            min_area=min_area,
            hits_only=hits_only,
        )
        if result is None:
            continue

        summary_rows.append([
            result["filename"],
            result["redacted_frames"],
            result["event_count"],
            result["first_timestamp"],
            result["last_timestamp"],
        ])

        for ev in result["events"]:
            all_event_rows.append([
                result["filename"],
                ev["start"],
                ev["end"],
                ev["black_boxes"],
                ev["white_boxes"],
            ])

    # --- Summary CSV (one row per file) ---
    if summary_rows:
        csv_path = os.path.join(output_folder, "video_summary.csv")
        with open(csv_path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["Filename", "Redacted_Frames", "Event_Count",
                             "First_Timestamp", "Last_Timestamp"])
            writer.writerows(summary_rows)
        print(f"\n Video summary CSV saved to: {csv_path}")

    # --- Detailed events CSV (one row per event) ---
    if all_event_rows:
        events_csv_path = os.path.join(output_folder, "video_events.csv")
        with open(events_csv_path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["Filename", "Start_Timestamp", "End_Timestamp",
                             "Black_Boxes", "White_Boxes"])
            writer.writerows(all_event_rows)
        print(f" Video events CSV saved to: {events_csv_path}")


# ===========================================================================
# Entry point
# ===========================================================================

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="PDF/Video Redaction Auditor: Removes vector/image/solid-color layers to find hidden content.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    parser.add_argument("-i", "--input",     type=str, help="Path to a single file or a folder.")
    parser.add_argument("-o", "--output",    type=str, help="Folder where cleaned files and CSVs will be saved.")
    parser.add_argument("-n", "--name",      type=str, help="Custom name for the output file (single-file mode only).")
    parser.add_argument("-b", "--bbox",      type=int, default=1, help="[PDF] Remove black/white vector boxes? (1=Yes, 0=No)")
    parser.add_argument("--highlight","--hl",type=int, default=1, help="[PDF] Highlight recovered text in red? (1=Yes, 0=No)")
    parser.add_argument("--hits",            action="store_true", help="Only save files where redactions were actually found.")
    parser.add_argument("--video",           action="store_true", help="Process video files instead of PDFs.")

    # Video tuning flags
    parser.add_argument("--black-thresh", type=int, default=15,
                        help="[Video] Pixel brightness threshold for black redactions (0-255, default 15).")
    parser.add_argument("--white-thresh", type=int, default=240,
                        help="[Video] Pixel brightness threshold for white redactions (0-255, default 240).")
    parser.add_argument("--min-area",     type=int, default=500,
                        help="[Video] Minimum pixel area for a region to be flagged as a redaction (default 500).")

    if '-h' in sys.argv or '--help' in sys.argv:
        parser.print_help()
        sys.exit(0)

    args = parser.parse_args()

    in_path = args.input  if args.input  else input("Input Path: ")
    in_path = in_path.strip().replace('"', '')

    out_dir = args.output if args.output else input("Output Folder: ")
    out_dir = out_dir.strip().replace('"', '')

    if args.video:
        run_video_operation(
            in_path, out_dir,
            custom_name=args.name,
            black_thresh=args.black_thresh,
            white_thresh=args.white_thresh,
            min_area=args.min_area,
            hits_only=args.hits,
        )
    else:
        run_operation(in_path, out_dir, args.bbox, args.highlight, args.name, hits_only=args.hits)
