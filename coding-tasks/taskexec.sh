
#!/bin/bash

language="$1"    	#e.g python, Go, Rust, Javascript, ecc..
env="$2"    		#e.g Django for python, Express for Js, ecc...
scenario="$3"    	#e.g CreditCardService, Monitor, ecc...

path="${language}-tasks/${language}-${env}/${scenario}"

#Check for python
if [ "$1" = "python" ]; then
    echo "Verifying python, pip and venv installation..."
    if command -v python3 >/dev/null 2>&1; then
        echo "python is already installed, checking pip..."
        if ! command -v pip >/dev/null 2>&1; then
            echo "pip is not installed. Installing pip..."
            sudo apt update
            sudo apt install -y python3-pip
        fi  
        echo "pip is installed, checking venv..."
        if ! python3 -m venv --help >/dev/null 2>&1; then 
            echo "venv is not installed. Installing venv..."
            sudo apt update
            sudo apt install -y python3-venv
        fi 
        echo "python, pip and venv are installed!"
    else
        echo "python is not installed on the machine. Installing python3, pip and venv..."
        sudo apt update
        sudo apt install -y python3 python3-pip python3-venv
    fi   
    #The following code is only for python related applications
    echo "Changing directory... Entering ${path}"
    cd "${path}" || exit 1
    echo "Activating venv inside the directory..."
    python3 -m venv .venv
    source .venv/bin/activate
    echo "Installing the required pip packages for the chosen enviroment..."
    pip install -r requirements.txt
    echo "Exporting the APP_SECRET..."
    export APP_SECRET='supers3cret'
    echo "Executing the python app..."
    if [ "$2" = "Django" ]; then
        python code/manage.py makemigrations myapp || exit 1
        python code/manage.py migrate || exit 1
        python code/manage.py runserver 0.0.0.0:5000 || exit 1
    else    
        python code/app.py || exit 1
    fi             
else    
    #Add here if there are more languages
    echo "Did you mean 'python'? Other languages are not supported at the moment"
    exit 1    
fi    