from storelimtless import StoreLimitlessClient, Directory, StoreLimitless

client: StoreLimitlessClient = StoreLimitless.login("iamloki", "whoami")
directory: Directory = client.directory
directory.mkdir("temp", missing_ok=True)
directory.cd("temp")
print(directory.upload("https://drive.google.com/file/d/1OP5AkfKzvugzV-dCb-HSN9o--FeloyaC/view?usp=drive_link", "Discord"))
directory.ls()[0].download()
