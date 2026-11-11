package com.github.miyako.sonar.fourd;

import java.io.DataInputStream;
import java.util.jar.Attributes;
import java.util.jar.JarFile;
import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertNull;

class PluginPackageIT {
  @Test
  void packagesManifestAndJava17ClassesWithoutBundlingApiOrTestLibraries() throws Exception {
    try (JarFile jar = new JarFile(System.getProperty("plugin.jar"))) {
      Attributes attributes = jar.getManifest().getMainAttributes();
      assertEquals("fourd", attributes.getValue("Plugin-Key"));
      assertEquals("4D Community Plugin for SonarQube", attributes.getValue("Plugin-Name"));
      assertEquals("com.github.miyako.sonar.fourd.FourDPlugin", attributes.getValue("Plugin-Class"));
      assertEquals("0.1.0-SNAPSHOT", attributes.getValue("Plugin-Version"));
      assertEquals("11.1.0.2693", attributes.getValue("Sonar-Version"));
      assertEquals("17", attributes.getValue("Jre-Min-Version"));
      assertNull(attributes.getValue("Plugin-RequiredForLanguages"));
      for (String name : new String[] {"FourDPlugin", "FourDLanguage"}) {
        var entry = jar.getJarEntry("com/github/miyako/sonar/fourd/" + name + ".class");
        assertNotNull(entry);
        try (DataInputStream bytes = new DataInputStream(jar.getInputStream(entry))) {
          assertEquals(0xCAFEBABE, bytes.readInt());
          bytes.readUnsignedShort();
          assertEquals(61, bytes.readUnsignedShort(), "Java 17 class-file major version");
        }
      }
      assertFalse(jar.stream().anyMatch(entry ->
          entry.getName().startsWith("org/sonar/")
              || entry.getName().startsWith("org/junit/")
              || entry.getName().startsWith("org/mockito/")
              || entry.getName().endsWith(".jar")));
    }
  }
}
