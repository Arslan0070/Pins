import csv
import json
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
import time

def load_config():
    with open('boards.json', 'r', encoding='utf-8') as f:
        return json.load(f)

def main():
    start_time = time.time()
    config = load_config()
    
    # Save directly into 'latest_run' folder as expected by your GitHub workflow
    output_dir = Path('latest_run')
    output_dir.mkdir(parents=True, exist_ok=True)
    
    font_path = 'assets/Poppins-Bold.ttf'
    pins = config.get('pins', [])
    
    if not pins:
        print("No pins found in configuration.")
        return
        
    print(f"Starting pin generation for {len(pins)} pins...")
    
    try:
        font_title = ImageFont.truetype(font_path, 48)
        font_subtitle = ImageFont.truetype(font_path, 24)
    except Exception:
        font_title = ImageFont.load_default()
        font_subtitle = ImageFont.load_default()
        
    results = []
    width, height = 800, 1200
    
    for index, pin_data in enumerate(pins):
        try:
            image = Image.new('RGB', (width, height), color=(255, 255, 255))
            draw = ImageDraw.Draw(image)
            
            title = pin_data.get('title', 'Pin Title')
            draw.text((50, 100), title, fill=(0, 0, 0), font=font_title)
            
            pin_id = pin_data.get('id', str(index))
            output_path = output_dir / f"{pin_id}.png"
            image.save(output_path, 'PNG', optimize=True)
            
            results.append({
                "id": pin_id,
                "title": title,
                "filename": f"{pin_id}.png",
                "status": "success"
            })
        except Exception as e:
            print(f"Error on pin {index}: {e}")
            
    # Name the CSV file pinterest_bulk_upload.csv to match your workflow search path: latest_run/pinterest_bulk_upload*.csv
    csv_path = output_dir / 'pinterest_bulk_upload.csv'
    with open(csv_path, mode='w', newline='', encoding='utf-8') as csv_file:
        fieldnames = ['id', 'title', 'filename', 'status']
        writer = csv.DictWriter(csv_file, fieldnames=fieldnames)
        writer.writeheader()
        for row in results:
            writer.writerow(row)
            
    print(f"Successfully generated {len(results)}/{len(pins)} pins and CSV report in {time.time() - start_time:.2f} seconds.")

if __name__ == '__main__':
    main()
