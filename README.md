NEURAL ENGINE

Small search engine for the Mac. Enter a place or an image term. The program fetches data from the internet, scores the results with NumPy, and displays them on a local website.This project does not use PyTorch or TensorFlow. It is also not Apple’s Neural Engine. The custom engine in engine.py only ranks the results. Every calculation is visible in the code and can be replaced later.What the program can do
Place search: Address, latitude, and longitude via OpenStreetMap (Nominatim, geopy library).
Image search: Title, image URL, preview, license, and author via Wikimedia Commons.
Local check: A found image is read with Pillow. NumPy calculates the average color.
Drop an image: Drag your own image file onto a local drop zone. The program finds the location itself (GPS, image analysis, metadata, filename, text).
Both services need an internet connection and no API key.

FilePurposemain.pyStarts the website in the browserwebsite.pyThe pages: selection, search, preview, linksStart.commandDouble-click in Finder, no VS Code neededengine.pyCalls the search and ranks the resultslocation_search.pyPlace searchimage_search.pyImage search and local image checkimage_drop.pyOlder drop zone. Replaced by the website.place_detection.pyLooks at the photo and recognizes the placeimage_location.pyPlace names from image data, filename, and texttools/read_text.swiftReads visible text on the Macrequirements.txtThe four librariesStart without VS CodeOpen the neural-engine folder in Finder and double-click Start.command.

The Terminal opens, then the website opens in the browser. The selection page appears. Each result opens its own HTML page with preview and links. A copy is saved in the seiten folder.The same commands work in any terminal:Bashcd /Users/jorzcanjo/neural-engine
source .venv/bin/activate
python main.pyStop with Ctrl+C in the terminal.Start in VS Code
Open VS Code.
File → Open Folder… and select the neural-engine folder.
Terminal → New Terminal.
Run these commands one after another in the terminal:
Bashpython3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python main.pysource .venv/bin/activate only applies to the current terminal. If you open a new terminal, run the activation again. The prompt then starts with (.venv).
Set the interpreter so the Run button uses the same environment:
Open the Command Palette with Cmd+Shift+P
Choose Python: Select Interpreter
Select the entry with .venv

Open main.py and start it with the Run button, or run python main.py in the activated terminal.
UsageIn the browser there are three options: Image search, Place search, Drop image.Clicking a result opens its own HTML page with preview and links (e.g. to the file, OpenStreetMap, and Apple Maps). The same page is saved as a file in the seiten folder and can be reopened in Finder. The start selection is also in seiten/auswahl.html.When you drop an image, drag the file from Finder onto the area. The program finds the location itself: first GPS, then by looking at the photo, then via image data, filename, and readable text. It shows latitude, longitude, address, and how the location was found. JPEG, PNG, GIF, and WebP work. Photos from iPhone are often HEIC and must be saved as JPEG first.Image recognition sends the photo to Grok. If you are signed in to Grok, the program uses that login. You do not need your own key. Without a login, GPS, filename, and text on the image still work.You can also stop with Ctrl+C.Extend it yourselfNew capability, for example a news search:
Create a new file that handles only this one task.
Add a method in NeuralEngine that calls the new module.
Add a new path on the start page in website.py.
Swap the scoring in cosine_scores and order_by_score. main.py does not need to know how the score is calculated.Notes on the services
Nominatim is meant for single queries, not many requests in a row.
Images stay on Wikimedia servers. The program does not store them.
Licenses are shown in the output. Check the file page before reusing.
