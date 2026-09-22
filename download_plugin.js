const https = require('https');
const fs = require('fs');
const path = require('path');
const { exec } = require('child_process');
const http = require('http');

// GitHub URL for the plugin
const GITHUB_URL = 'https://github.com/NanSsye/DependencyManager';
const PLUGIN_NAME = 'DependencyManager';

// Directory where plugins are stored
const PLUGINS_DIR = path.join(__dirname, 'plugins');

// Create plugins directory if it doesn't exist
if (!fs.existsSync(PLUGINS_DIR)) {
    fs.mkdirSync(PLUGINS_DIR, { recursive: true });
    console.log(`Created plugins directory: ${PLUGINS_DIR}`);
}

// Path for the zip file
const TEMP_ZIP_PATH = path.join(__dirname, `${PLUGIN_NAME}.zip`);
const PLUGIN_DIR = path.join(PLUGINS_DIR, PLUGIN_NAME);

// Function to download the GitHub repository
function downloadGitHubRepo() {
    return new Promise((resolve, reject) => {
        console.log(`Downloading ${GITHUB_URL}...`);
        
        // Format ZIP URL from GitHub repository URL
        // Convert https://github.com/user/repo to https://github.com/user/repo/archive/refs/heads/main.zip
        const zipUrl = `${GITHUB_URL}/archive/refs/heads/main.zip`;
        
        // Download the ZIP file
        const file = fs.createWriteStream(TEMP_ZIP_PATH);
        
        https.get(zipUrl, (response) => {
            // Check if redirection
            if (response.statusCode === 302 || response.statusCode === 301) {
                const redirectUrl = response.headers.location;
                console.log(`Redirecting to ${redirectUrl}`);
                
                // Handle both HTTPS and HTTP redirects
                const client = redirectUrl.startsWith('https') ? https : http;
                
                client.get(redirectUrl, (redirectResponse) => {
                    redirectResponse.pipe(file);
                    
                    file.on('finish', () => {
                        file.close();
                        console.log(`Downloaded to ${TEMP_ZIP_PATH}`);
                        resolve(TEMP_ZIP_PATH);
                    });
                }).on('error', (err) => {
                    fs.unlink(TEMP_ZIP_PATH, () => {}); // Delete the file on error
                    reject(err);
                });
            } else if (response.statusCode === 200) {
                response.pipe(file);
                
                file.on('finish', () => {
                    file.close();
                    console.log(`Downloaded to ${TEMP_ZIP_PATH}`);
                    resolve(TEMP_ZIP_PATH);
                });
            } else {
                reject(new Error(`Failed to download: Status code ${response.statusCode}`));
            }
        }).on('error', (err) => {
            fs.unlink(TEMP_ZIP_PATH, () => {}); // Delete the file on error
            reject(err);
        });
    });
}

// Function to extract the zip file using PowerShell (Windows) or unzip (Linux/Mac)
function extractZip(zipPath, destPath) {
    return new Promise((resolve, reject) => {
        console.log(`Extracting ${zipPath} to ${destPath}...`);
        
        // Delete target directory if it exists
        if (fs.existsSync(destPath)) {
            console.log(`Removing existing directory: ${destPath}`);
            fs.rmSync(destPath, { recursive: true, force: true });
        }
        
        // Create target directory
        fs.mkdirSync(destPath, { recursive: true });
        
        // On Windows, use PowerShell to extract
        const isWindows = process.platform === 'win32';
        let command;
        
        if (isWindows) {
            // Use PowerShell's Expand-Archive
            command = `powershell -command "Expand-Archive -Path '${zipPath}' -DestinationPath '${PLUGINS_DIR}' -Force"`;
        } else {
            // Use unzip on Linux/Mac
            command = `unzip -o "${zipPath}" -d "${PLUGINS_DIR}"`;
        }
        
        exec(command, (error, stdout, stderr) => {
            if (error) {
                console.error(`Extraction error: ${error.message}`);
                return reject(error);
            }
            
            console.log(`Extraction stdout: ${stdout}`);
            
            if (stderr) {
                console.error(`Extraction stderr: ${stderr}`);
            }
            
            // Files extracted to a subdirectory with '-main' suffix, need to rename
            const extractedDir = path.join(PLUGINS_DIR, `${PLUGIN_NAME}-main`);
            
            if (fs.existsSync(extractedDir)) {
                // Rename the extracted directory to the plugin name
                if (fs.existsSync(destPath)) {
                    fs.rmSync(destPath, { recursive: true, force: true });
                }
                fs.renameSync(extractedDir, destPath);
                console.log(`Renamed ${extractedDir} to ${destPath}`);
            } else {
                console.warn(`Warning: Expected directory ${extractedDir} not found after extraction`);
            }
            
            resolve(destPath);
        });
    });
}

// Function to clean up temporary files
function cleanup() {
    if (fs.existsSync(TEMP_ZIP_PATH)) {
        fs.unlinkSync(TEMP_ZIP_PATH);
        console.log(`Deleted temporary file: ${TEMP_ZIP_PATH}`);
    }
}

// Main function to coordinate the process
async function installPlugin() {
    try {
        const zipPath = await downloadGitHubRepo();
        await extractZip(zipPath, PLUGIN_DIR);
        cleanup();
        
        console.log(`\nPlugin installation complete!`);
        console.log(`Plugin installed to: ${PLUGIN_DIR}`);
        console.log(`\nYou may need to restart your application to activate the plugin.`);
    } catch (error) {
        console.error(`Error installing plugin: ${error.message}`);
        cleanup();
    }
}

// Run the installation
installPlugin(); 