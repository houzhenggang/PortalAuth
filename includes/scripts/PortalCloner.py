import os
import re
import sys
import shutil

parent_dir = os.path.abspath(os.path.dirname(__file__))
libs_dir = os.path.join(parent_dir, 'libs')
sys.path.append(libs_dir)

import requests
import urlparse
import tinycss
import collections
from bs4 import BeautifulSoup

class PortalCloner:
	
	def __init__(self, portalName, directory, injectSet):
		self.portalName = portalName
		self.portalDirectory = directory + self.portalName + "/"
		self.resourceDirectory = self.portalDirectory + "resources/"
		self.injectionSet = injectSet
		self.css_urls = collections.defaultdict(list)
		self.splashFile = self.portalDirectory + "index.php"
		self.url = None
		self.soup = None
		self.session = requests.Session()
		self.basePath = '/pineapple/modules/PortalAuth/'
		
		
		
		
	def find_meta_refresh(self, r):
		soup = BeautifulSoup(r.text, "html.parser")
		for meta in soup.find_all("meta"):
			if meta.has_attr("http-equiv"):
				if "url=" in meta.get("content").lower():
					text = meta.get("content").split(";")[1]
					text = text.strip()
					if text.lower().startswith("url="):
						new_url=text[4:]
						return True, new_url
		return False, r
	

	
	def follow_redirects(self, r,s):
		redirected, new_url = self.find_meta_refresh(r)
		if redirected:
			r = self.follow_redirects(self.session.get(urlparse.urljoin(r.url, new_url)), s)
		return r
		
		
		
	def downloadFile(self, r,name):
		with open(self.resourceDirectory + name, 'wb') as out_file:
			for chunk in r.iter_content(4096):
				out_file.write(chunk)
			
			
			
	def parseCSS(self, url):
		r = requests.get(url)
		urls = []
		parser = tinycss.make_parser('page3')
		try:
			stylesheet = parser.parse_stylesheet(r.text)
			for rule in stylesheet.rules:
				for dec in rule.declarations:
					for token in dec.value:
						if token.type == "URI":
							# Strip out anything not part of the URL and append it to the list
							urls.append(token.as_css().replace("url(","").replace(")","").strip('"\''))
		except:
			pass
		return urls
		
		
		
	def checkFileName(self, orig):
		filename, file_ext = os.path.splitext(orig)
		path = self.resourceDirectory + filename + file_ext
		fname = orig
		uniq = 1
		while os.path.exists(path):
			fname = "%s_%d%s" % (filename, uniq, file_ext)
			path = self.resourceDirectory + fname
			uniq += 1
		return fname
		
		
		
	def fetchPage(self, url):
		# Check if the proper directories exist and create them if not
		for path in [self.portalDirectory, self.resourceDirectory]:
			if not os.path.exists(path):
				os.makedirs(path)
				
		# Attempt to open an external web page and load the HTML
		response = requests.get(url, verify=False)

		# Get the actual URL - This accounts for redirects - and set the class variable with it
		self.url = response.url

		# Set up the URL as our referrer to get access to protected images
		self.session.headers.update({'referer':self.url})

		# Follow any meta refreshes that exist before continuing
		response = self.follow_redirects(response, self.session)

		# Create a BeautifulSoup object to hold our HTML structure
		self.soup = BeautifulSoup(response.text, "html.parser")




		
	def cloneResources(self):
		# Download all linked JS files and remove all inline JavaScript
		for script in self.soup.find_all('script'):
			if script.has_attr('src'):
				fname = str(script.get('src')).split("/")[-1]
				r = self.session.get(urlparse.urljoin(self.url, script.get('src')), stream=True, verify=False)
				self.downloadFile(r,fname)
				script['src'] = "resources/" + fname
				
		# Search through all tags for the style attribute and gather inline CSS references
		for tag in self.soup():
			if tag.has_attr('style'):
				for dec in tag['style'].split(";"):
					token = dec.split(":")[-1]
					token = token.strip()
					if token.lower().startswith("url"):
						imageURL = token.replace("url(","").replace(")","").strip('"\'')
						fname = imageURL.split("/")[-1]
						r = self.session.get(urlparse.urljoin(self.url, imageURL), stream=True, verify=False)
						self.downloadFile(r, fname)

						# Change the inline CSS
						tag['style'].replace(imageURL, "resources/" + fname)
					
		# Search for CSS files linked with the @import statement and remove
		for style in self.soup.find_all("style"):
			parser = tinycss.make_parser('page3')
			stylesheet = parser.parse_stylesheet(style.string)
			for rule in stylesheet.rules:
				if rule.at_keyword == "@import":
					fname = str(rule.uri).split("/")[-1]
					r = self.session.get(urlparse.urljoin(self.url, rule.uri), stream=True, verify=False)
					self.downloadFile(r, fname)
					
					# Parse the CSS to get image links
					_key = "resources/" + fname
					self.css_urls[_key] = self.parseCSS(urlparse.urljoin(self.url, rule.uri))
					
					# Replace the old link of the CSS with the new one
					modStyle = style.string
					style.string.replace_with(modStyle.replace(rule.uri, "resources/" + fname))
		
		
		# Find and download all images and CSS files linked with <link>
		for img in self.soup.find_all(['img', 'link', 'embed']):
			if img.has_attr('href'):
				tag = "href"
			elif img.has_attr('src'):
				tag = "src"
			
			# Parse the tag to get the file name
			fname = str(img.get(tag)).split("/")[-1]
			
			# Strip out any undesired characters
			pattern = re.compile('[^a-zA-Z0-9_.]+', re.UNICODE)
			fname = pattern.sub('', fname)
			fname = fname[:255]
			
			if fname == "":
				continue
			if fname.rpartition('.')[1] == "":
				fname += ".css"
			if fname.rpartition('.')[2] == "css":
				_key = "resources/" + fname
				self.css_urls[_key] = self.parseCSS(urlparse.urljoin(self.url, img.get(tag)))

			# Download the file
			checkedName = self.checkFileName(fname)
			r = self.session.get(urlparse.urljoin(self.url, img.get(tag)), stream=True, verify=False)
			self.downloadFile(r, checkedName)
			
			# Change the image src to look for the image in resources
			img[tag] = "resources/" + checkedName
			
		# Download any images found in the CSS file and change the link to resources
		# This occurs AFTER the CSS files have already been copied
		for css_file, urls in self.css_urls.iteritems():

			# Open the CSS file and get the contents
			fh = open(self.portalDirectory + css_file).read().decode('utf-8')

			# Iterate over the URLs associated with this CSS file
			for _fileurl in urls:

				# Get the image name
				fname = _fileurl.split("/")[-1]

				# Download the image from the web server
				checkedName = self.checkFileName(fname)
				try:
					r = self.session.get(urlparse.urljoin(self.url, _fileurl), stream=True, verify=False)
					self.downloadFile(r, checkedName)
				except:
					pass
				
				# Change the link in the CSS file
				fh = fh.replace(_fileurl, checkedName)

			# Write the contents back out to the file
			fw = open(self.portalDirectory + css_file, 'w')
			fw.write(fh.encode('utf-8'))
			fw.flush()
			fw.close()
		
		
		
		
	def stripJS(self):
		for script in self.soup.find_all('script'):
			script.clear()
	
	
	
	def stripCSS(self):
		for tag in self.soup():
			if tag.has_attr('style'):
				tag['style'] = ""
		
		for style in self.soup.find_all("style"):
			style.clear()
			

	
	def stripLinks(self):
		# Find and clear all href attributes from a tags
		for link in self.soup.find_all('a'):
			link['href'] = ""
		
		
		
	def stripForms(self):
		# Find all forms, remove the action and clear the form
		for form in self.soup.find_all('form'):
			# Clear the action attribute
			form['action'] = ""

			# Clear the form
			form.clear()
		
		
	
	def injectJS(self):
		# Add user defined functions from injectJS.txt
		with open(self.basePath + 'includes/scripts/injects/' + self.injectionSet + '/injectJS.txt', 'r') as injectJS:
			self.soup.head.append(injectJS.read())
		
		
		
	def injectCSS(self):
		# Add user defined CSS from injectCSS.txt
		with open(self.basePath + 'includes/scripts/injects/' + self.injectionSet + '/injectCSS.txt', 'r') as injectCSS:
			self.soup.head.append(injectCSS.read())
		
		
		
	def injectHTML(self):
		# Append our HTML elements to the body of the web page
		with open(self.basePath + 'includes/scripts/injects/' + self.injectionSet + '/injectHTML.txt', 'r') as injectHTML:
			self.soup.body.append(injectHTML.read())
		
		
	def writeFiles(self):
		# Write the file out to index.php
		with open(self.splashFile, 'w') as splash:
			with open(self.basePath + 'includes/scripts/injects/' + self.injectionSet + '/injectPHP.txt', 'r') as injectPHP:
				splash.write(injectPHP.read())
			splash.write((self.soup.prettify(formatter=None)).encode('utf-8'))
			
		# Copy the MyPortal PHP script to portalDirectory
		shutil.copy(self.basePath + 'includes/scripts/injects/' + self.injectionSet + '/MyPortal.php', self.portalDirectory)

		# Create the required .ep file
		with open(self.portalDirectory + self.portalName + ".ep", 'w+') as epFile:
			epFile.write("DO NOT DELETE THIS")
			
		# Copy jquery to the portal directory
		shutil.copy(self.basePath + 'includes/scripts/jquery-2.2.1.min.js', self.portalDirectory)
		
		